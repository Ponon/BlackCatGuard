"""首页读取配置和事件，历史检测不能当成实时在线状态。"""
import unittest
from starlette.requests import Request
from tortoise import Tortoise

from main import app
from dao.models import Administrator, GroupConfig, VerificationSettings, WelcomeSettings, KeywordSettings, AISettings, EventLog
from routers.pages import home
from services.dashboard import build_dashboard


class DashboardTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        await Tortoise.init(db_url='sqlite://:memory:', modules={'models': ['dao.models']})
        await Tortoise.generate_schemas()
        await Administrator.create(id=1, username='admin', password_hash='test')
        self.request = Request({'type': 'http', 'method': 'GET', 'path': '/', 'headers': [], 'session': {'admin_id': 1}, 'router': app.router, 'scheme': 'http', 'server': ('localhost', 80), 'query_string': b''})

    async def asyncTearDown(self):
        await Tortoise.close_connections()

    async def test_empty_and_unauthorized(self):
        data = await build_dashboard()
        self.assertEqual(data['status'], '未配置')
        self.assertEqual(data['enabled_count'], 0)
        self.assertEqual(data['events'], [])
        html = (await home(self.request)).body.decode()
        self.assertIn('这里还很安静', html)
        self.assertIn('href="/events"', html)
        self.request.session.clear()
        self.assertEqual((await home(self.request)).headers['location'], '/login')

    async def test_saved_configuration_and_detection(self):
        await GroupConfig.create(id=1, bot_token='secret', chat_id='-1001', revision='test')
        data = await build_dashboard()
        self.assertEqual(data['bot_state'], '正在检测')
        self.assertEqual(data['status'], '正在检测')
        await GroupConfig.filter(id=1).update(check_result={'ok': True, 'bot_name': '小猫', 'group_name': '测试群', 'checks': [{'label': '机器人连接', 'ok': True}]})
        data = await build_dashboard()
        self.assertEqual(data['status'], '正在检测')
        self.assertEqual(data['group_name'], '测试群')
        self.assertEqual(data['bot_note'], '小猫')
        html = (await home(self.request)).body.decode()
        self.assertNotIn('secret', html)
        self.assertIn('最近检测通过', html)
        await GroupConfig.filter(id=1).update(check_result={'ok': False, 'checks': []})
        self.assertEqual((await build_dashboard())['status'], '正在检测')

    async def test_feature_switches_depend_on_group_and_complete_settings(self):
        await VerificationSettings.create(id=1, enabled=True)
        await WelcomeSettings.create(id=1, enabled=True)
        await KeywordSettings.create(id=1, enabled=True, keywords=['广告'])
        await AISettings.create(id=1, enabled=True, base_url='', model='')
        self.assertEqual((await build_dashboard())['enabled_count'], 0)
        await GroupConfig.create(id=1, bot_token='secret', chat_id='-1001', revision='test')
        data = await build_dashboard()
        self.assertEqual(data['enabled_count'], 3)
        self.assertFalse(data['features'][2]['enabled'])
        await AISettings.filter(id=1).update(base_url='http://localhost/v1', model='test')
        self.assertEqual((await build_dashboard())['enabled_count'], 4)
        await GroupConfig.all().delete()
        self.assertEqual((await build_dashboard())['enabled_count'], 0)

    async def test_recent_events_order_limit_escape_and_no_fake_counts(self):
        for index in range(7):
            await EventLog.create(event_type='消息测试', username='<script>bad</script>', result=f'结果{index}')
        data = await build_dashboard()
        self.assertEqual(len(data['events']), 5)
        self.assertEqual(data['events'][0]['result'], '结果6')
        self.assertEqual(data['members_today'], '—')
        self.assertEqual(data['blocked_today'], '—')
        html = (await home(self.request)).body.decode()
        self.assertNotIn('<script>bad</script>', html)
        self.assertIn('&lt;script&gt;bad&lt;/script&gt;', html)

    async def test_incomplete_member_settings_are_not_shown_as_enabled(self):
        await GroupConfig.create(id=1, bot_token='secret', chat_id='-1001', revision='test')
        await VerificationSettings.create(id=1, enabled=True, method='unknown', timeout_seconds=60)
        await WelcomeSettings.create(id=1, enabled=True, message='', delete_after_seconds=60)
        self.assertEqual((await build_dashboard())['enabled_count'], 0)
        await VerificationSettings.filter(id=1).update(method='button', timeout_seconds=1)
        await WelcomeSettings.filter(id=1).update(message='欢迎', delete_after_seconds=-1)
        self.assertEqual((await build_dashboard())['enabled_count'], 0)
