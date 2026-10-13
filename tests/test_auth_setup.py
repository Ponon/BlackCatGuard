"""初始化同意流程使用内存数据库，不启动后台网络任务。"""
import json
import time
import unittest
from starlette.requests import Request
from tortoise import Tortoise
from fastapi import HTTPException
from main import app
from dao.models import Administrator
from routers.auth import setup_page, setup_consent, setup_submit, login_submit, TERMS_VERSION
from utils.security import verify_password


class SetupTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        await Tortoise.init(db_url='sqlite://:memory:', modules={'models': ['dao.models']})
        await Tortoise.generate_schemas()
        self.request = Request({'type': 'http', 'method': 'POST', 'path': '/setup',
            'headers': [(b'accept', b'application/json')], 'session': {'csrf_token': 'token'},
            'router': app.router, 'scheme': 'http', 'server': ('localhost', 80),
            'query_string': b'step=create'})

    async def asyncTearDown(self):
        await Tortoise.close_connections()

    async def create(self, **changes):
        values = dict(username='admin', password='password123', confirm_password='password123', csrf_token='token')
        values.update(changes)
        return await setup_submit(self.request, **values)

    async def agree(self):
        return await setup_consent(self.request, csrf_token='token', accepted='yes', terms_version=TERMS_VERSION)

    async def test_cannot_skip_consent(self):
        html = (await setup_page(self.request)).body.decode()
        self.assertIn('使用须知全文', html)
        self.assertNotIn('id="password"', html)
        self.assertEqual((await self.create()).status_code, 403)
        self.assertFalse(await Administrator.exists())

    async def test_requires_explicit_current_consent_and_csrf(self):
        for accepted, version in [('', TERMS_VERSION), ('yes', 'old')]:
            response = await setup_consent(self.request, csrf_token='token', accepted=accepted, terms_version=version)
            self.assertEqual(response.status_code, 400)
            self.assertNotIn('setup_consent', self.request.session)
        with self.assertRaises(HTTPException):
            await setup_consent(self.request, csrf_token='wrong', accepted='yes', terms_version=TERMS_VERSION)

    async def test_two_steps_create_then_login(self):
        await self.agree()
        html = (await setup_page(self.request)).body.decode()
        self.assertIn('id="confirm_password"', html)
        self.assertNotIn('使用须知全文', html)
        response = await self.create()
        self.assertEqual(json.loads(response.body)['redirect'], '/login')
        admin = await Administrator.get(id=1)
        self.assertTrue(verify_password('password123', admin.password_hash))
        self.assertEqual(self.request.session, {})
        self.request.session['csrf_token'] = 'new'
        response = await login_submit(self.request, username='admin', password='password123', csrf_token='new')
        self.assertEqual(json.loads(response.body)['redirect'], '/')
        self.assertEqual(self.request.session['admin_id'], 1)
        self.assertEqual((await self.agree()).status_code, 303)
        self.assertEqual(await Administrator.all().count(), 1)

    async def test_expired_consent_and_long_password(self):
        await self.agree()
        self.request.session['setup_consent']['accepted_at'] = time.time() - 3601
        self.assertEqual((await self.create()).status_code, 403)
        await self.agree()
        response = await self.create(password='a' * 129, confirm_password='a' * 129)
        self.assertEqual(response.status_code, 400)
        self.assertFalse(await Administrator.exists())
