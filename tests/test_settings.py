"""代理配置与公共网络入口测试，不发起真实外部连接。"""
import unittest
from unittest.mock import patch, Mock
from urllib.request import Request

from fastapi import HTTPException
from starlette.requests import Request as WebRequest
from tortoise import Tortoise

from dao.models import Administrator, SystemSettings, GroupConfig
from main import app
from routers.settings import save_settings, settings_page, validate_proxy
from routers.group import check_config
from services.network import ExplicitProxyHandler, get_proxy_url, open_request


class SettingsTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        await Tortoise.init(db_url='sqlite://:memory:', modules={'models': ['dao.models']})
        await Tortoise.generate_schemas()
        await Administrator.create(id=1, username='admin', password_hash='test')
        self.request = WebRequest({
            'type': 'http', 'method': 'POST', 'path': '/settings', 'headers': [],
            'session': {'admin_id': 1, 'csrf_token': 'token'}, 'router': app.router,
            'scheme': 'http', 'server': ('localhost', 80), 'query_string': b'',
        })

    async def asyncTearDown(self):
        await Tortoise.close_connections()

    async def test_save_clear_and_render(self):
        self.assertEqual(await get_proxy_url(), '')
        await save_settings(self.request, ' http://127.0.0.1:7897 ', 'token')
        self.assertEqual(await get_proxy_url(), 'http://127.0.0.1:7897')
        html = (await settings_page(self.request)).body.decode()
        self.assertIn('http://127.0.0.1:7897', html)
        self.assertIn('aria-current="page"', html)
        await save_settings(self.request, '', 'token')
        self.assertEqual(await get_proxy_url(), '')
        self.assertEqual(await SystemSettings.all().count(), 1)

    async def test_auth_csrf_and_invalid_input(self):
        with self.assertRaises(HTTPException) as error:
            await save_settings(self.request, '', 'wrong')
        self.assertEqual(error.exception.status_code, 403)
        self.request.session.clear()
        with self.assertRaises(HTTPException) as error:
            await save_settings(self.request, '', 'token')
        self.assertEqual(error.exception.status_code, 401)
        for value in ['socks5://localhost:1080', 'localhost:7897', 'http://host:99999', 'http://host:80/path', 'http://host:80?x=1', 'http://host:80\nHeader: x']:
            with self.assertRaises(HTTPException):
                validate_proxy(value)
        self.assertEqual(validate_proxy('https://proxy.example:443'), 'https://proxy.example:443')

    async def test_detection_uses_latest_proxy(self):
        await GroupConfig.create(id=1, bot_token='test', chat_id='-1001', revision='test')
        for proxy in ['http://127.0.0.1:7897', '']:
            await save_settings(self.request, proxy, 'token')
            with patch('routers.group.inspect_group', return_value={'ok': True}) as inspect:
                await check_config(self.request, 'token')
                inspect.assert_called_once_with('test', '-1001', proxy)


class NetworkTests(unittest.TestCase):
    def test_explicit_proxy_and_default(self):
        for proxy in ['http://localhost:7897', '']:
            with patch('services.network.build_opener') as build:
                open_request(Request('https://api.telegram.org'), proxy_url=proxy)
                handler = build.call_args.args[0]
                if proxy:
                    self.assertIsInstance(handler, ExplicitProxyHandler)
                    self.assertEqual(handler.proxies, {'http': proxy, 'https': proxy})
                else:
                    self.assertNotIsInstance(handler, ExplicitProxyHandler)

    def test_explicit_proxy_does_not_bypass(self):
        request = Request('https://api.telegram.org/botREDACTED/getMe')
        handler = ExplicitProxyHandler({'https': 'http://127.0.0.1:7897'})
        with patch.dict('os.environ', {'no_proxy': '*'}):
            handler.proxy_open(request, 'http://127.0.0.1:7897', 'https')
        self.assertEqual(request.host, '127.0.0.1:7897')
        self.assertEqual(request._tunnel_host, 'api.telegram.org')
