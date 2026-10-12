"""管理员资料修改测试，使用内存数据库。"""
import unittest

from fastapi import HTTPException
from starlette.requests import Request
from tortoise import Tortoise

from dao.models import Administrator
from main import app
from routers.settings import update_account, settings_page
from utils.security import hash_password, verify_password


class AccountTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        await Tortoise.init(db_url='sqlite://:memory:', modules={'models': ['dao.models']})
        await Tortoise.generate_schemas()
        await Administrator.create(id=1, username='admin', password_hash=hash_password('password123'))
        self.request = Request({
            'type': 'http', 'method': 'POST', 'path': '/settings/account', 'headers': [],
            'session': {'admin_id': 1, 'csrf_token': 'token'}, 'router': app.router,
            'scheme': 'http', 'server': ('localhost', 80), 'query_string': b'',
        })

    async def asyncTearDown(self):
        await Tortoise.close_connections()

    async def update(self, **kwargs):
        values = dict(username='new_admin', current_password='password123', new_password='', confirm_password='', csrf_token='token')
        values.update(kwargs)
        return await update_account(self.request, **values)

    async def test_username_only(self):
        original = (await Administrator.get(id=1)).password_hash
        response = await self.update()
        admin = await Administrator.get(id=1)
        self.assertEqual(admin.username, 'new_admin')
        self.assertEqual(admin.password_hash, original)
        self.assertEqual(response['redirect'], '/login')
        self.assertEqual(self.request.session, {})

    async def test_password_only(self):
        await self.update(username='admin', new_password='newpassword123', confirm_password='newpassword123')
        admin = await Administrator.get(id=1)
        self.assertEqual(admin.username, 'admin')
        self.assertTrue(verify_password('newpassword123', admin.password_hash))
        self.assertFalse(verify_password('password123', admin.password_hash))

    async def test_both(self):
        await self.update(new_password='newpassword123', confirm_password='newpassword123')
        admin = await Administrator.get(id=1)
        self.assertEqual(admin.username, 'new_admin')
        self.assertTrue(verify_password('newpassword123', admin.password_hash))

    async def test_invalid_input_does_not_change_account(self):
        for values in [dict(current_password='wrong'), dict(username='!'), dict(new_password='short', confirm_password='short'), dict(new_password='password456', confirm_password='different'), dict(csrf_token='wrong'), dict(username='admin')]:
            with self.assertRaises(HTTPException):
                await self.update(**values)
            admin = await Administrator.get(id=1)
            self.assertEqual(admin.username, 'admin')
            self.assertTrue(verify_password('password123', admin.password_hash))
        html = (await settings_page(self.request)).body.decode()
        self.assertIn('id="account-form"', html)
        self.assertNotIn('password123', html)

    async def test_requires_login(self):
        self.request.session.clear()
        with self.assertRaises(HTTPException) as error:
            await self.update()
        self.assertEqual(error.exception.status_code, 401)
