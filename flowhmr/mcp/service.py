"""Own the local MCP child process; never create another inference worker."""
import atexit
import asyncio
from contextlib import suppress
from http.client import HTTPConnection, HTTPException
import json
import os
from pathlib import Path
import secrets
import subprocess
import sys
import threading
import time
import psutil
from .contract import INSTANCE_ENV, SERVICE_KEY_ENV, SERVICE_KEY_HEADER, TOOL_NAMES


def local_request(port, method, path, *, headers=None, timeout=0.5):
    connection = HTTPConnection('127.0.0.1', port, timeout=timeout)
    try:
        connection.request(method, path, headers=headers or {})
        with connection.getresponse() as response:
            return response.status, json.loads(response.read(4096))
    finally:
        connection.close()


class MCPService:
    def __init__(self, root):
        self.root = Path(root)
        self.directory = self.root / '.cache/mcp'
        self.process = None
        self.active_port = None
        self.instance = None
        self.service_key = secrets.token_urlsafe(32)
        self.startup_error = None
        self.on_change = None
        self.lock = threading.RLock()
        atexit.register(self.stop)

    def _config(self):
        path = self.directory / 'settings.json'
        return json.loads(path.read_text('utf-8')) if path.exists() else {'port': 60551}

    def _token(self):
        path = self.directory / 'token'
        if not path.exists():
            self.directory.mkdir(parents=True, exist_ok=True)
            if sys.platform == 'win32':
                account = subprocess.check_output(['whoami'], text=True).strip()
                subprocess.run(['icacls', str(self.directory), '/inheritance:r', '/grant:r',
                                f'{account}:(OI)(CI)F'], check=True, capture_output=True,
                               creationflags=subprocess.CREATE_NO_WINDOW)
            path.write_text(secrets.token_urlsafe(32), encoding='ascii')
            if sys.platform != 'win32':
                path.chmod(0o600)
        return path.read_text('ascii').strip()

    def snapshot(self):
        with self.lock:
            config = self._config()
            alive = self.process is not None and self.process.poll() is None
            port = self.active_port if alive else config['port']
            auth_enabled = config.get('auth_enabled', False)
            return {'running': alive, 'port': port, 'url': f'http://127.0.0.1:{port}/mcp',
                    'auto_start': config.get('auto_start', False), 'startup_error': self.startup_error,
                    'auth_enabled': auth_enabled,
                    'token': self._token() if auth_enabled else '', 'tools': TOOL_NAMES}

    def configure_auto_start(self, enabled):
        if type(enabled) is not bool:
            raise ValueError('auto_start must be a boolean.')
        with self.lock:
            config = {**self._config(), 'auto_start': enabled}
            self.directory.mkdir(parents=True, exist_ok=True)
            self.directory.joinpath('settings.json').write_text(json.dumps(config), encoding='utf-8')
            return self.snapshot()

    def start_if_configured(self, backend_url):
        with self.lock:
            config = self._config()
            if config.get('auto_start', False):
                return self.start(config['port'], backend_url)

    def configure_auth(self, enabled):
        if type(enabled) is not bool:
            raise ValueError('auth_enabled must be a boolean.')
        with self.lock:
            if self.process is not None and self.process.poll() is None:
                raise ValueError('Stop MCP before changing authentication.')
            config = {**self._config(), 'auth_enabled': enabled}
            if enabled:
                self._token()
            self.directory.mkdir(parents=True, exist_ok=True)
            self.directory.joinpath('settings.json').write_text(json.dumps(config), encoding='utf-8')
            return self.snapshot()

    def start(self, port, backend_url):
        if type(port) is not int or not 1024 <= port <= 65535:
            raise ValueError('Port must be an integer between 1024 and 65535.')
        with self.lock:
            if self.process is not None and self.process.poll() is None:
                raise ValueError('MCP is already running. Stop it before changing settings.')
            self.startup_error = None
            auth_enabled = self._config().get('auth_enabled', False)
            if auth_enabled:
                self._token()
            self.directory.mkdir(parents=True, exist_ok=True)
            logs = self.root / 'logs'
            logs.mkdir(exist_ok=True)
            instance = secrets.token_urlsafe(32)
            with (logs / 'mcp.log').open('a', encoding='utf-8') as log:
                self.process = subprocess.Popen(
                    [sys.executable, '-m', 'flowhmr.mcp.server', '--port', str(port),
                     '--backend-url', backend_url, '--parent-pid', str(os.getpid()),
                     *(['--auth-enabled'] if auth_enabled else [])],
                    cwd=self.root, stdout=log, stderr=log,
                    env={**os.environ, INSTANCE_ENV: instance, SERVICE_KEY_ENV: self.service_key},
                    creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == 'win32' else 0)
            self.active_port, self.instance = port, instance
            def watch(process):
                process.wait()
                if self.on_change is not None:
                    self.on_change()
            threading.Thread(target=watch, args=(self.process,), daemon=True).start()
            try:
                deadline = time.monotonic() + 15
                while time.monotonic() < deadline:
                    if self.process.poll() is not None:
                        raise RuntimeError('MCP startup failed. See logs/mcp.log.')
                    # Windows venv launchers have a different PID from the interpreter.
                    if self._is_ready(port, instance):
                        self.directory.joinpath('settings.json').write_text(
                            json.dumps({**self._config(), 'port': port, 'auth_enabled': auth_enabled}), encoding='utf-8')
                        return self.snapshot()
                    time.sleep(0.1)
                raise RuntimeError('MCP startup timed out. See logs/mcp.log.')
            except Exception:
                self.stop()
                raise

    @staticmethod
    def _is_ready(port, instance):
        try:
            status, body = local_request(port, 'GET', '/ready')
            return status == 200 and isinstance(body, dict) and body.get('instance') == instance
        except (HTTPException, OSError, ValueError):
            return False

    def stop(self):
        with self.lock:
            if self.process is not None and self.process.poll() is None:
                stopped = False
                if self.active_port is not None and self._is_ready(self.active_port, self.instance):
                    with suppress(HTTPException, OSError, ValueError):
                        status, _ = local_request(self.active_port, 'POST', '/shutdown',
                                                  headers={SERVICE_KEY_HEADER: self.service_key}, timeout=5)
                        if status == 202:
                            with suppress(subprocess.TimeoutExpired):
                                self.process.wait(timeout=1205)
                                stopped = True
                if not stopped:
                    self._terminate_process_tree()
            self.process = None
            self.active_port = None
            self.instance = None

    def _terminate_process_tree(self):
        children = []
        with suppress(psutil.NoSuchProcess):
            children = psutil.Process(self.process.pid).children(recursive=True)
        for child in reversed(children):
            with suppress(psutil.NoSuchProcess):
                child.terminate()
        if self.process.poll() is None:
            self.process.terminate()
            self.process.wait(timeout=5)
        psutil.wait_procs(children, timeout=2)

    def reset_token(self):
        with self.lock:
            if not self._config().get('auth_enabled', False):
                raise ValueError('Enable token authentication before resetting its token.')
            if self.process is not None and self.process.poll() is None:
                raise ValueError('Stop MCP before resetting its token.')
            self._token()
            self.directory.joinpath('token').write_text(secrets.token_urlsafe(32), encoding='ascii')
            return self.snapshot()

    def test_connection(self):
        state = self.snapshot()
        if not state['running']:
            raise ValueError('Start MCP before testing its connection.')

        async def check():
            import httpx
            from mcp import ClientSession
            from mcp.client.streamable_http import streamable_http_client
            headers = {'Authorization': f"Bearer {state['token']}"} if state['auth_enabled'] else {}
            async with httpx.AsyncClient(headers=headers,
                                         trust_env=False) as client:
                async with streamable_http_client(state['url'], http_client=client) as (read, write, _):
                    async with ClientSession(read, write) as session:
                        info = await session.initialize()
                        tools = await session.list_tools()
                        result = await session.call_tool('flowhmr_get_status', {})
                        if result.isError:
                            raise RuntimeError(str(result.content))
                        return {'server': info.serverInfo.name, 'protocol_version': info.protocolVersion,
                                'tools': [tool.name for tool in tools.tools], 'status': result.structuredContent}

        return asyncio.run(asyncio.wait_for(check(), timeout=20))
