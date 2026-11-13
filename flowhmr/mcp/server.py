"""Local Streamable HTTP MCP endpoint. Run using the project's Python runtime."""
import argparse
import asyncio
import hmac
import os
import sys
import re
from contextlib import asynccontextmanager
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import psutil
import uvicorn
from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings
from starlette.responses import JSONResponse
from flowhmr.mcp.backend import Backend, fail
from flowhmr.mcp.contract import INSTANCE_ENV, SERVICE_KEY_ENV, SERVICE_KEY_HEADER
from flowhmr.mcp.exports import Exports
from flowhmr.mcp.tools import build_tools


def create_app(backend_url, port, token=None, export_directory=None, shutdown=None, *,
               service_key=None, instance=None):
    backend = Backend(backend_url, service_key)
    exports = Exports(export_directory or Path(__file__).resolve().parents[2] / '.cache/mcp/exports', backend)

    mcp = FastMCP('flowhmr_mcp', stateless_http=True, json_response=True,
                  tools=build_tools(backend, exports),
                  transport_security=TransportSecuritySettings(
                      enable_dns_rebinding_protection=True,
                      allowed_hosts=[f'127.0.0.1:{port}', f'localhost:{port}'],
                      allowed_origins=[f'http://127.0.0.1:{port}', f'http://localhost:{port}']))

    @mcp.custom_route('/health', methods=['GET'])
    async def health(request):
        return JSONResponse({'pid': os.getpid()})

    @mcp.custom_route('/ready', methods=['GET'])
    async def ready(request):
        return JSONResponse({'instance': instance})

    @mcp.custom_route('/shutdown', methods=['POST'])
    async def stop(request):
        if shutdown is None:
            return JSONResponse({'error': 'Shutdown is not configured'}, status_code=409)
        shutdown()
        return JSONResponse({'stopping': True}, status_code=202)

    @mcp.resource('flowhmr://jobs/{job_id}/people/{index}', mime_type='image/jpeg')
    async def person_thumbnail(job_id: str, index: int) -> bytes:
        """Candidate/person thumbnail for visual selection; index is zero-based."""
        if not re.fullmatch(r'[A-Za-z0-9_-]{1,64}', job_id) or index < 0:
            fail('invalid_params', 'Invalid job ID or candidate index.')
        async with backend.client() as client:
            response = await client.get(f'/api/jobs/{job_id}/person/{index}.jpg')
            await backend.check(response)
            return response.content

    app = mcp.streamable_http_app()
    transport_lifespan = app.router.lifespan_context

    @asynccontextmanager
    async def lifespan(application):
        # FastMCP's own lifespan runs per session/request in stateless mode.
        # Export recovery belongs to the HTTP process lifetime, not a tool call.
        exports.recover()
        if exports.rows:
            await exports.notify()
        async with transport_lifespan(application):
            try:
                yield
            finally:
                await exports.close()

    app.router.lifespan_context = lifespan

    class LocalAuth:
        def __init__(self, app):
            self.app = app

        async def __call__(self, scope, receive, send):
            if scope['type'] == 'http':
                headers = dict(scope['headers'])
                host = headers.get(b'host', b'').decode()
                origin = headers.get(b'origin')
                if host not in (f'127.0.0.1:{port}', f'localhost:{port}') or (
                        origin is not None and origin.decode() not in (
                            f'http://127.0.0.1:{port}', f'http://localhost:{port}')):
                    await JSONResponse({'error': 'Invalid Host or Origin'}, status_code=403)(scope, receive, send)
                    return
                path = scope['path']
                if path == '/shutdown' and service_key is not None:
                    if not hmac.compare_digest(headers.get(SERVICE_KEY_HEADER.lower().encode(), b''),
                                               service_key.encode()):
                        await JSONResponse({'error': 'MCP service key required'}, status_code=403)(scope, receive, send)
                        return
                elif (path != '/ready' or instance is None) and token is not None and not hmac.compare_digest(
                        headers.get(b'authorization', b''), f'Bearer {token}'.encode()):
                    await JSONResponse({'error': 'Bearer token required'}, status_code=401)(scope, receive, send)
                    return
            await self.app(scope, receive, send)

    return LocalAuth(app)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--port', type=int, default=60551)
    parser.add_argument('--backend-url', default='http://127.0.0.1:60550')
    parser.add_argument('--parent-pid', type=int)
    parser.add_argument('--auth-enabled', action='store_true', help='Require the saved Bearer token')
    args = parser.parse_args()
    token = ((Path(__file__).resolve().parents[2] / '.cache/mcp/token').read_text('ascii').strip()
             if args.auth_enabled else None)
    app = create_app(args.backend_url, args.port, token, shutdown=lambda: setattr(server, 'should_exit', True),
                     service_key=os.environ.get(SERVICE_KEY_ENV), instance=os.environ.get(INSTANCE_ENV))
    server = uvicorn.Server(uvicorn.Config(app, host='127.0.0.1', port=args.port, access_log=False))

    async def run():
        parent = psutil.Process(args.parent_pid) if args.parent_pid else None

        async def watch_parent():
            while not server.should_exit:
                if parent is not None and not parent.is_running():
                    server.should_exit = True
                    return
                await asyncio.sleep(1)

        watcher = asyncio.create_task(watch_parent())
        try:
            await server.serve()
        finally:
            watcher.cancel()

    asyncio.run(run())


if __name__ == '__main__':
    main()
