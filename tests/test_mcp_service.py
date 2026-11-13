"""Exercise the real HTTP MCP transport without loading GPU models."""
import asyncio
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import socket
import threading
import time
import tempfile
import unittest
from unittest.mock import patch

import httpx
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client
import uvicorn
from flowhmr.mcp.service import MCPService
from flowhmr.mcp.contract import TOOL_NAMES
from flowhmr.mcp.server import create_app


class Backend(BaseHTTPRequestHandler):
    unavailable = False

    def do_POST(self):
        self.rfile.read(int(self.headers.get('Content-Length', 0)))
        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.end_headers()
        self.wfile.write(b'{"id":"test-receipt"}')

    def do_GET(self):
        self.send_response(503 if self.unavailable else 200)
        self.send_header('Content-Type', 'application/json')
        self.end_headers()
        self.wfile.write(json.dumps({'device': 'test', 'runtime': {'ready': True},
                                    'models_loaded': False, 'defaults': {'steps': 20},
                                    'model_files': {'items': []}}).encode())

    def log_message(self, *args):
        pass


class MCPTransportTest(unittest.TestCase):
    token = 'test-secret'

    @classmethod
    def setUpClass(cls):
        cls.backend = ThreadingHTTPServer(('127.0.0.1', 0), Backend)
        cls.backend_thread = threading.Thread(target=cls.backend.serve_forever, daemon=True)
        cls.backend_thread.start()
        cls.sock = socket.socket()
        cls.sock.bind(('127.0.0.1', 0))
        port = cls.sock.getsockname()[1]
        cls.url = f'http://127.0.0.1:{port}'
        cls.storage = tempfile.TemporaryDirectory()
        app = create_app(f'http://127.0.0.1:{cls.backend.server_port}', port, cls.token,
                         export_directory=cls.storage.name)
        cls.server = uvicorn.Server(uvicorn.Config(app, log_level='error'))
        cls.thread = threading.Thread(target=cls.server.run, kwargs={'sockets': [cls.sock]}, daemon=True)
        cls.thread.start()
        deadline = time.monotonic() + 5
        while not cls.server.started:
            if time.monotonic() > deadline:
                raise RuntimeError('Test MCP server did not start')
            time.sleep(0.01)

    @classmethod
    def tearDownClass(cls):
        cls.server.should_exit = True
        cls.thread.join(5)
        cls.sock.close()
        cls.backend.shutdown()
        cls.backend.server_close()
        cls.backend_thread.join(5)
        cls.storage.cleanup()

    def test_auth_and_origin(self):
        with httpx.Client(trust_env=False) as client:
            expected = 401 if self.token else 200
            self.assertEqual(client.get(self.url + '/health').status_code, expected)
            headers = {'Authorization': 'Bearer wrong'}
            self.assertEqual(client.get(self.url + '/health', headers=headers).status_code, expected)
            headers = {'Authorization': 'Bearer test-secret', 'Origin': 'https://evil.example'}
            self.assertEqual(client.get(self.url + '/health', headers=headers).status_code, 403)
            headers = {'Authorization': 'Bearer test-secret', 'Host': 'evil.example'}
            self.assertEqual(client.get(self.url + '/health', headers=headers).status_code, 403)

    def test_real_mcp_client(self):
        async def run():
            headers = {'Authorization': f'Bearer {self.token}'} if self.token else {}
            async with httpx.AsyncClient(headers=headers, trust_env=False) as client:
                async with streamable_http_client(self.url + '/mcp', http_client=client) as (read, write, _):
                    async with ClientSession(read, write) as session:
                        initialized = await session.initialize()
                        self.assertEqual(initialized.serverInfo.name, 'flowhmr_mcp')
                        tools = await session.list_tools()
                        self.assertEqual(set(tool.name for tool in tools.tools), set(TOOL_NAMES))
                        result = await session.call_tool('flowhmr_get_status', {})
                        self.assertFalse(result.isError)
                        self.assertEqual(result.structuredContent['device'], 'test')
                        self.assertTrue(result.structuredContent['backend_ready'])
                        Backend.unavailable = True
                        try:
                            failed = await session.call_tool('flowhmr_get_status', {})
                            self.assertTrue(failed.isError)
                        finally:
                            Backend.unavailable = False
        asyncio.run(run())


class MCPWithoutTokenTest(MCPTransportTest):
    token = None


class MCPSettingsTest(unittest.TestCase):
    def test_auto_start_opt_in_and_persistence(self):
        with tempfile.TemporaryDirectory() as directory:
            service = MCPService(directory)
            self.assertFalse(service.snapshot()['auto_start'])
            with patch.object(service, 'start') as start:
                service.start_if_configured('http://127.0.0.1:60550')
                start.assert_not_called()
            service.configure_auto_start(True)
            service.configure_auth(False)
            restored = MCPService(directory)
            self.assertTrue(restored.snapshot()['auto_start'])
            with patch.object(restored, 'start') as start:
                restored.start_if_configured('http://127.0.0.1:60550')
                start.assert_called_once_with(60551, 'http://127.0.0.1:60550')
            restored.configure_auto_start(False)
            self.assertFalse(MCPService(directory).snapshot()['auto_start'])
            with self.assertRaises(ValueError):
                restored.configure_auto_start('true')

    def test_default_and_saved_auth(self):
        with tempfile.TemporaryDirectory() as directory:
            service = MCPService(directory)
            self.assertFalse(service.snapshot()['auth_enabled'])
            self.assertFalse(service.directory.joinpath('token').exists())
            enabled = service.configure_auth(True)
            self.assertTrue(enabled['auth_enabled'])
            self.assertTrue(enabled['token'])
            self.assertTrue(MCPService(directory).snapshot()['auth_enabled'])
            disabled = service.configure_auth(False)
            self.assertFalse(disabled['auth_enabled'])
            self.assertEqual(disabled['token'], '')

    def test_old_settings_default_to_no_auth(self):
        with tempfile.TemporaryDirectory() as directory:
            service = MCPService(directory)
            service.directory.mkdir(parents=True)
            service.directory.joinpath('settings.json').write_text('{"port": 60551}', encoding='utf-8')
            self.assertFalse(service.snapshot()['auth_enabled'])
