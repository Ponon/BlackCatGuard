"""所有后台页面的结构、静态资源及新增字段迁移检查。"""
import unittest
from pathlib import Path
from html.parser import HTMLParser
from starlette.requests import Request
from tortoise import Tortoise
from dao.models import Administrator, VerificationSettings, JoinSession
from dao.database import migrate_join_actions
from main import app
from routers.pages import home
from routers.group import group_page
from routers.verification import verification_page
from routers.antispam import antispam_page
from routers.broadcast import broadcast_page
from routers.events import events_page
from routers.settings import settings_page

ROOT = Path(__file__).resolve().parents[1]


class PageParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.ids = []
        self.resources = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if 'id' in attrs:
            self.ids.append(attrs['id'])
        if tag in ('img', 'script', 'link'):
            url = attrs.get('src') or attrs.get('href', '')
            if url.startswith('http://localhost/static/') or url.startswith('/static/'):
                self.resources.append(url.split('/static/', 1)[1].split('?', 1)[0])


class CompletionTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        await Tortoise.init(db_url='sqlite://:memory:', modules={'models': ['dao.models']})
        await Tortoise.generate_schemas()
        await Administrator.create(id=1, username='admin', password_hash='fake')
        self.request = Request({'type': 'http', 'method': 'GET', 'path': '/', 'headers': [],
            'session': {'admin_id': 1, 'csrf_token': 'token'}, 'router': app.router,
            'scheme': 'http', 'server': ('localhost', 80), 'query_string': b''})

    async def asyncTearDown(self):
        await Tortoise.close_connections()

    async def test_pages_render_with_unique_ids_and_existing_assets(self):
        for page in [home, group_page, verification_page, antispam_page, broadcast_page, events_page, settings_page]:
            with self.subTest(page=page.__name__):
                response = await page(self.request)
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.headers['Cache-Control'], 'no-store')
                parser = PageParser()
                parser.feed(response.body.decode())
                self.assertEqual(len(parser.ids), len(set(parser.ids)))
                for resource in parser.resources:
                    self.assertTrue((ROOT / 'static' / resource).is_file(), resource)

    async def test_old_database_gains_actions_and_retry_fields_without_data_loss(self):
        await VerificationSettings.create(id=1, enabled=True, method='arithmetic', timeout_seconds=90)
        await JoinSession.create(id='existing', bot_key='fake', chat_id='-1001', user_id=123, user={'id': 123}, state='pending')
        connection = Tortoise.get_connection('default')
        for table, columns in [('verification_settings', ['timeout_action', 'wrong_action']),
                               ('join_sessions', ['failure_action', 'failure_reason', 'retry_at', 'failure_count', 'config_revision'])]:
            for column in columns:
                await connection.execute_query(f'ALTER TABLE "{table}" DROP COLUMN "{column}"')
        await migrate_join_actions()
        await migrate_join_actions()
        settings = await VerificationSettings.get(id=1)
        self.assertEqual((settings.method, settings.timeout_seconds, settings.timeout_action, settings.wrong_action),
                         ('arithmetic', 90, 'kick', 'none'))
        session = await JoinSession.get(id='existing')
        self.assertEqual((session.state, session.retry_at, session.failure_count), ('pending', 0, 0))
