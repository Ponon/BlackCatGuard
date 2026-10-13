"""群配置测试使用内存数据库，不接触本地真实账号和 Telegram。"""
import json
import unittest
from unittest.mock import patch

from fastapi import HTTPException
from starlette.requests import Request
from tortoise import Tortoise

from dao.models import Administrator, GroupConfig, VerificationSettings, WelcomeSettings
from main import app
from routers.group import check_config, group_page, save_config
from services.telegram import TelegramError, inspect_group

TOKEN = '123456:' + 'a' * 35


class GroupTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.inspector = patch('routers.group.inspect_group', return_value={'ok': True, 'message': '检测通过', 'checks': []})
        self.inspector.start()
        await Tortoise.init(db_url='sqlite://:memory:', modules={'models': ['dao.models']})
        await Tortoise.generate_schemas()
        await Administrator.create(id=1, username='admin', password_hash='test')
        self.request = Request({
            'type': 'http', 'method': 'POST', 'path': '/group', 'headers': [],
            'query_string': b'', 'scheme': 'http', 'server': ('localhost', 80),
            'session': {'admin_id': 1, 'csrf_token': 'token'}, 'router': app.router,
        })

    async def asyncTearDown(self):
        self.inspector.stop()
        await Tortoise.close_connections()

    async def save(self):
        return await save_config(self.request, '-1001234567890', TOKEN, 'token')

    async def test_save_and_clear_results(self):
        await self.save()
        config = await GroupConfig.get(id=1)
        config.check_result = {'ok': True}
        await config.save()
        await save_config(self.request, '-1002222222222', TOKEN, 'token')
        config = await GroupConfig.get(id=1)
        self.assertEqual(config.bot_token, TOKEN)
        self.assertTrue(config.check_result['ok'])
        self.assertEqual(await GroupConfig.all().count(), 1)
        html = (await group_page(self.request)).body.decode()
        self.assertIn(TOKEN, html)
        self.assertIn('type="password"', html)
        self.assertIn('id="toggle-token"', html)
        self.assertIn('-1002222222222', html)

    async def test_delete_and_partial_input(self):
        await self.save()
        for chat, token in [('-1001', ''), ('', TOKEN)]:
            with self.assertRaises(HTTPException):
                await save_config(self.request, chat, token, 'token')
        self.assertEqual(await GroupConfig.all().count(), 1)
        await VerificationSettings.create(id=1, enabled=True)
        await WelcomeSettings.create(id=1, enabled=True)
        result = await save_config(self.request, '', '', 'token')
        self.assertFalse((await VerificationSettings.get(id=1)).enabled)
        self.assertFalse((await WelcomeSettings.get(id=1)).enabled)
        self.assertFalse(result['configured'])
        self.assertEqual(await GroupConfig.all().count(), 0)
        with self.assertRaises(HTTPException):
            await check_config(self.request, 'token')

    async def test_save_runs_detection(self):
        result = await self.save()
        self.assertTrue(result['check_result']['ok'])
        with patch('routers.group.inspect_group', return_value={'ok': False, 'message': '网络失败', 'checks': []}):
            result = await self.save()
        self.assertTrue(result['configured'])
        self.assertFalse(result['check_result']['ok'])

    async def test_saved_failure_message_and_shared_live_health(self):
        from services.runtime_health import health_snapshot
        with patch('routers.group.inspect_group', return_value={'ok': False, 'message': '连接超时', 'checks': []}):
            result = await self.save()
        self.assertIn('保存成功', result['message'])
        self.assertIn('检测未通过', result['message'])
        self.assertFalse(result['check_result']['ok'])
        self.assertEqual((await health_snapshot())['state'], 'unstable')
        with patch('routers.group.inspect_group', return_value={'ok': True, 'message': '通过', 'checks': [], 'latency_ms': 123}):
            await check_config(self.request, 'token')
        snapshot = await health_snapshot()
        self.assertEqual(snapshot['state'], 'online')
        self.assertEqual(snapshot['latency_ms'], 123)

    async def test_proxy_change_during_manual_check_rejects_old_result(self):
        from dao.models import SystemSettings
        await self.save()
        async def change_proxy(*args):
            await SystemSettings.create(id=1, proxy_url='http://localhost:7897')
            return {'ok': True, 'checks': []}
        with patch('routers.group.run_in_threadpool', side_effect=change_proxy):
            with self.assertRaises(HTTPException) as error:
                await check_config(self.request, 'token')
        self.assertEqual(error.exception.status_code, 409)

    async def test_access_validation_and_csrf(self):
        for chat, token, csrf, status in [('-1001', TOKEN, 'bad', 403), ('https://t.me/test', TOKEN, 'token', 400), ('-1001', 'bad', 'token', 400)]:
            with self.assertRaises(HTTPException) as error:
                await save_config(self.request, chat, token, csrf)
            self.assertEqual(error.exception.status_code, status)
        self.assertEqual(await GroupConfig.all().count(), 0)
        with self.assertRaises(HTTPException):
            await check_config(self.request, 'token')
        self.request.session.clear()
        with self.assertRaises(HTTPException) as error:
            await self.save()
        self.assertEqual(error.exception.status_code, 401)
        self.assertEqual((await group_page(self.request)).headers['location'], '/login')

    async def test_check_saves_result_and_rejects_stale_config(self):
        await self.save()
        result = {'ok': True, 'message': '通过', 'checks': []}
        with patch('routers.group.inspect_group', return_value=result):
            self.assertEqual(await check_config(self.request, 'token'), result)
        self.assertEqual((await GroupConfig.get(id=1)).check_result, result)
        async def replace_config(*args):
            await GroupConfig.filter(id=1).update(revision='new-version')
            return result
        with patch('routers.group.run_in_threadpool', side_effect=replace_config):
            with self.assertRaises(HTTPException) as error:
                await check_config(self.request, 'token')
            self.assertEqual(error.exception.status_code, 409)


    async def test_switch_group_stops_features_and_old_sessions_but_same_config_preserves(self):
        from dao.models import BroadcastContent, BroadcastTask, JoinSession, KeywordSettings
        await self.save()
        await VerificationSettings.create(id=1, enabled=True)
        await KeywordSettings.create(id=1, enabled=True, keywords=['广告'])
        content = await BroadcastContent.create(name='公告', message='正文')
        await BroadcastTask.create(content=content, enabled=True)
        await JoinSession.create(id='old', bot_key='old', chat_id='-1001234567890', user_id=1, user={'id': 1}, state='pending')
        await self.save()
        self.assertTrue((await VerificationSettings.get()).enabled)
        await save_config(self.request, '-1002', TOKEN, 'token')
        self.assertFalse((await VerificationSettings.get()).enabled)
        self.assertFalse((await KeywordSettings.get()).enabled)
        self.assertFalse((await BroadcastTask.get()).enabled)
        self.assertEqual((await JoinSession.get()).state, 'done')


class TelegramTests(unittest.TestCase):
    def responses(self, permissions=True, chat_type='supergroup'):
        return [
            {'id': 1, 'first_name': 'Bot', 'username': 'test_bot'},
            {'title': '测试群', 'type': chat_type},
            {'status': 'administrator', 'can_delete_messages': permissions,
             'can_restrict_members': permissions, 'can_invite_users': permissions},
        ]

    def test_success_and_missing_permissions(self):
        for expected in (True, False):
            with patch('services.telegram.call_api', side_effect=self.responses(expected)):
                result = inspect_group(TOKEN, '-1001')
            self.assertEqual(result['ok'], expected)
            self.assertEqual(len(result['checks']), 6)

    def test_non_group_and_connection_errors(self):
        with patch('services.telegram.call_api', side_effect=self.responses(chat_type='channel')):
            result = inspect_group(TOKEN, '-1001')
        self.assertFalse(result['ok'])
        with patch('services.telegram.call_api', side_effect=TelegramError('连接失败')):
            result = inspect_group(TOKEN, '-1001')
        self.assertFalse(result['ok'])
        self.assertNotIn(TOKEN, json.dumps(result))
