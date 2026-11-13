"""Own the local MCP child process; never create another inference worker."""
import atexit
import asyncio
import json
import os
from pathlib import Path
import secrets
import subprocess
import sys
import threading
import time
from urllib.error import URLError
from urllib.request import Request, urlopen
from .contract import TOOL_NAMES


class MCPService:
    def __init__(self, root):
        self.root = Path(root)
        self.directory = self.root / '.cache/mcp'
        self.process = None
        self.active_port = None
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
            token = self._token() if auth_enabled else None
            self.directory.mkdir(parents=True, exist_ok=True)
            logs = self.root / 'logs'
            logs.mkdir(exist_ok=True)
            with (logs / 'mcp.log').open('a', encoding='utf-8') as log:
                self.active_port = port
                self.process = subprocess.Popen(
                    [sys.executable, '-m', 'flowhmr.mcp.server', '--port', str(port),
                     '--backend-url', backend_url, '--parent-pid', str(os.getpid()),
                     *(['--auth-enabled'] if auth_enabled else [])],
                    cwd=self.root, stdout=log, stderr=log,
                    creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == 'win32' else 0)
            def watch(process):
                process.wait()
                if self.on_change is not None:
                    self.on_change()
            threading.Thread(target=watch, args=(self.process,), daemon=True).start()
            deadline = time.monotonic() + 15
            while time.monotonic() < deadline:
                if self.process.poll() is not None:
                    raise RuntimeError('MCP startup failed. See logs/mcp.log.')
                try:
                    req = Request(f'http://127.0.0.1:{port}/health',
                                  headers={'Authorization': f'Bearer {token}'} if auth_enabled else {})
                    with urlopen(req, timeout=0.5) as response:
                        result = json.load(response)
                    if result.get('pid') == self.process.pid:
                        self.directory.joinpath('settings.json').write_text(
                            json.dumps({**self._config(), 'port': port, 'auth_enabled': auth_enabled}), encoding='utf-8')
                        return self.snapshot()
                except (URLError, TimeoutError, OSError):
                    pass  # Child has not bound the socket yet; bounded readiness polling.
                time.sleep(0.1)
            self.stop()
            raise RuntimeError('MCP startup timed out. See logs/mcp.log.')

    def stop(self):
        with self.lock:
            if self.process is not None and self.process.poll() is None:
                state = self.snapshot()
                headers = {'Authorization': f"Bearer {state['token']}"} if state['auth_enabled'] else {}
                with urlopen(Request(state['url'].removesuffix('/mcp') + '/shutdown',
                                     data=b'', headers=headers, method='POST'), timeout=5):
                    pass
                self.process.wait(timeout=1205)
            self.process = None
            self.active_port = None

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
