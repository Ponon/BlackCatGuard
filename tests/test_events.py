"""白名单优先级、日志分页与访问控制。"""
import unittest
from datetime import datetime
from zoneinfo import ZoneInfo
from types import SimpleNamespace
from unittest.mock import patch

from fastapi import HTTPException
from starlette.requests import Request
from tortoise import Tortoise

from main import app
from dao.models import Administrator, EventLog, WhitelistSettings
from routers.antispam import save_whitelist
from routers.events import events_page, clear_events
from services.antispam import classify_message
from dao.models import event_time_now, VerificationSettings
from routers.verification import save_verification


class WhitelistDecisionTests(unittest.TestCase):
    def test_whitelist_skips_all_rules(self):
        keyword = SimpleNamespace(enabled=True, keywords=['广告'])
        ai = SimpleNamespace(enabled=True)
        with patch('services.antispam.classify_ai') as classify:
            for identity in ({'user_id': '123'}, {'username': '@ALICE'}):
                result = classify_message('广告', keyword, ai, whitelist=['123', '@alice'], **identity)
                self.assertFalse(result['is_spam'])
                self.assertEqual(result['checks'][0]['source'], '白名单')
            classify.assert_not_called()
        result = classify_message('广告', keyword, None, whitelist=['123'], user_id='1234')
        self.assertTrue(result['is_spam'])


class EventsTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        await Tortoise.init(db_url='sqlite://:memory:', modules={'models': ['dao.models']})
        await Tortoise.generate_schemas()
        await Administrator.create(id=1, username='admin', password_hash='test')
        self.request = Request({'type': 'http', 'method': 'GET', 'path': '/events', 'headers': [], 'session': {'admin_id': 1, 'csrf_token': 'token'}, 'router': app.router, 'scheme': 'http', 'server': ('localhost', 80), 'query_string': b''})

    async def asyncTearDown(self):
        await Tortoise.close_connections()

    async def test_whitelist_validation_and_clear(self):
        await save_whitelist(self.request, '123\n@Alice\n@alice', 'token')
        self.assertEqual((await WhitelistSettings.get(id=1)).entries, ['123', '@Alice'])
        with self.assertRaises(HTTPException):
            await save_whitelist(self.request, '用户名', 'token')
        with self.assertRaises(HTTPException):
            await save_whitelist(self.request, '123', 'wrong')
        await save_whitelist(self.request, '', 'token')
        self.assertEqual((await WhitelistSettings.get(id=1)).entries, [])

    async def test_pagination_order_boundaries_and_escaping(self):
        for index in range(25):
            await EventLog.create(event_type='消息测试', nickname='<script>bad</script>', username='@alice', result=f'记录-{index}')
        response = await events_page(self.request, page=1, page_size=10)
        self.assertEqual(len(response.context['rows']), 10)
        self.assertEqual(response.context['rows'][0]['result'], '记录-24')
        self.assertEqual(response.context['pages'], 3)
        self.assertNotIn('<script>bad</script>', response.body.decode())
        response = await events_page(self.request, page=999, page_size=10)
        self.assertEqual(response.context['page'], 3)
        self.assertEqual(len(response.context['rows']), 5)
        response = await events_page(self.request, page=-1, page_size=999)
        self.assertEqual(response.context['page'], 1)
        self.assertEqual(response.context['page_size'], 10)

    async def test_empty_and_unauthorized(self):
        response = await events_page(self.request)
        self.assertIn('暂无事件记录', response.body.decode())
        self.assertEqual(response.context['pages'], 1)
        self.request.session.clear()
        response = await events_page(self.request)
        self.assertEqual(response.headers['location'], '/login')

    async def test_beijing_time(self):
        await EventLog.create(event_type='消息测试', created_at=datetime(2026, 10, 12, 16, 44, 35, tzinfo=ZoneInfo('Asia/Shanghai')), result='正常')
        response = await events_page(self.request)
        self.assertEqual(response.context['rows'][0]['time'], '2026-10-12 16:44:35')
        self.assertLess(abs((event_time_now() - datetime.now(ZoneInfo('Asia/Shanghai'))).total_seconds()), 2)

    async def test_config_changes_do_not_create_logs(self):
        await VerificationSettings.create(id=1, enabled=False, method='button', timeout_seconds=60)
        await save_verification(self.request, 'false', 'button', 'token', '120')
        self.assertEqual(await EventLog.all().count(), 0)
        await save_whitelist(self.request, '123', 'token')
        self.assertEqual(await EventLog.all().count(), 0)

    async def test_clear_requires_confirmation_auth_and_csrf(self):
        await EventLog.create(event_type='消息测试', content='测试', result='正常')
        for token, confirmed in [('wrong', 'true'), ('token', '')]:
            with self.assertRaises(HTTPException):
                await clear_events(self.request, token, confirmed)
            self.assertEqual(await EventLog.all().count(), 1)
        await clear_events(self.request, 'token', 'true')
        self.assertEqual(await EventLog.all().count(), 0)
        response = await events_page(self.request)
        self.assertEqual(response.context['success'], '所有日志已清空。')
        self.request.session.clear()
        with self.assertRaises(HTTPException) as error:
            await clear_events(self.request, 'token', 'true')
        self.assertEqual(error.exception.status_code, 401)
