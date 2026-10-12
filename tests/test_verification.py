"""验证配置的独立保存、访问控制与页面回显。"""
import unittest
from fastapi import HTTPException
from starlette.requests import Request
from tortoise import Tortoise
from dao.models import Administrator, VerificationSettings, WelcomeSettings, GroupConfig
from main import app
from routers.verification import save_verification, save_welcome, verification_page


class VerificationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        await Tortoise.init(db_url='sqlite://:memory:', modules={'models': ['dao.models']})
        await Tortoise.generate_schemas()
        await Administrator.create(id=1, username='admin', password_hash='test')
        await GroupConfig.create(id=1, bot_token='test', chat_id='-1001', revision='test')
        self.request = Request({'type': 'http', 'method': 'POST', 'path': '/verification', 'headers': [], 'session': {'admin_id': 1, 'csrf_token': 'token'}, 'router': app.router, 'scheme': 'http', 'server': ('localhost', 80), 'query_string': b''})

    async def asyncTearDown(self):
        await Tortoise.close_connections()

    async def test_enable_change_disable_and_render(self):
        await save_verification(self.request, 'true', 'button', 'token')
        self.assertTrue((await VerificationSettings.get(id=1)).enabled)
        await save_verification(self.request, 'true', 'arithmetic', 'token')
        await save_verification(self.request, 'false', 'arithmetic', 'token')
        settings = await VerificationSettings.get(id=1)
        self.assertFalse(settings.enabled)
        self.assertEqual(settings.method, 'arithmetic')
        self.assertEqual(await VerificationSettings.all().count(), 1)
        html = (await verification_page(self.request)).body.decode()
        self.assertIn('value="arithmetic" checked', html)
        self.assertIn('入群处理说明', html)
        self.assertIn('role="switch"', html)

    async def test_invalid_values_and_csrf(self):
        for enabled, method, csrf in [('true', 'invalid', 'token'), ('invalid', 'button', 'token'), ('true', 'button', 'bad')]:
            with self.assertRaises(HTTPException):
                await save_verification(self.request, enabled, method, csrf)
        self.assertEqual(await VerificationSettings.all().count(), 0)

    async def test_independent_welcome_and_verification(self):
        await save_verification(self.request, 'true', 'arithmetic', 'token', '120')
        await save_welcome(self.request, 'true', '你好 {nickname}，{username}', '0', 'token')
        await save_verification(self.request, 'false', 'button', 'token', '60')
        welcome = await WelcomeSettings.get(id=1)
        self.assertTrue(welcome.enabled)
        self.assertEqual(welcome.message, '你好 {nickname}，{username}')
        self.assertEqual(welcome.delete_after_seconds, 0)
        await save_welcome(self.request, 'false', '欢迎 {nickname}', '60', 'token')
        verification = await VerificationSettings.get(id=1)
        self.assertFalse(verification.enabled)
        self.assertEqual(verification.method, 'button')
        for welcome, deletion in [('', '60'), ('x', '-1'), ('x', '86401')]:
            with self.assertRaises(HTTPException):
                await save_welcome(self.request, 'true', welcome, deletion, 'token')
        html = (await verification_page(self.request)).body.decode()
        self.assertIn('id="welcome-form"', html)
        self.assertIn('id="verification-form"', html)

    async def test_existing_schema_migration(self):
        from dao.database import migrate_verification_settings
        connection = Tortoise.get_connection('default')
        await connection.execute_query('DROP TABLE verification_settings')
        await connection.execute_query('CREATE TABLE verification_settings (id INTEGER PRIMARY KEY, enabled INT NOT NULL, method VARCHAR(16) NOT NULL)')
        await connection.execute_query("INSERT INTO verification_settings VALUES (1, 1, 'arithmetic')")
        await migrate_verification_settings()
        await migrate_verification_settings()
        settings = await VerificationSettings.get(id=1)
        self.assertEqual(settings.method, 'arithmetic')
        self.assertTrue(settings.enabled)
        self.assertEqual(settings.timeout_seconds, 60)
        from dao.database import migrate_welcome_settings
        await migrate_welcome_settings()
        welcome = await WelcomeSettings.get(id=1)
        self.assertEqual(welcome.delete_after_seconds, 60)
        await save_welcome(self.request, 'true', '自定义欢迎', '90', 'token')
        await migrate_welcome_settings()
        self.assertEqual((await WelcomeSettings.get(id=1)).message, '自定义欢迎')

    async def test_enable_requires_group(self):
        await GroupConfig.all().delete()
        for action in (
            lambda: save_verification(self.request, 'true', 'button', 'token'),
            lambda: save_welcome(self.request, 'true', '欢迎', '60', 'token'),
        ):
            with self.assertRaises(HTTPException) as error:
                await action()
            self.assertEqual(error.exception.status_code, 409)
        await save_verification(self.request, 'false', 'button', 'token')
        await save_welcome(self.request, 'false', '欢迎', '60', 'token')
        self.assertFalse((await VerificationSettings.get(id=1)).enabled)
        self.assertFalse((await WelcomeSettings.get(id=1)).enabled)
        html = (await verification_page(self.request)).body.decode()
        self.assertIn('data-group-configured="false"', html)

    async def test_unauthenticated(self):
        self.request.session.clear()
        self.assertEqual((await verification_page(self.request)).headers['location'], '/login')
        with self.assertRaises(HTTPException) as error:
            await save_verification(self.request, 'true', 'button', 'token')
        self.assertEqual(error.exception.status_code, 401)

    async def test_saved_state_and_normalized_response(self):
        response = await verification_page(self.request)
        self.assertFalse(response.context['verification_saved'])
        self.assertFalse(response.context['welcome_saved'])
        result = await save_verification(self.request, 'true', 'arithmetic', 'token', '0120')
        self.assertEqual(result['timeout_seconds'], 120)
        result = await save_welcome(self.request, 'true', '欢迎 {nickname}', '0', 'token')
        self.assertTrue(result['enabled'])
        self.assertEqual(result['delete_after_seconds'], 0)
        response = await verification_page(self.request)
        self.assertTrue(response.context['verification_saved'])
        self.assertTrue(response.context['welcome_saved'])
        await GroupConfig.filter(id=1).update(bot_token='')
        response = await verification_page(self.request)
        self.assertFalse(response.context['group_configured'])
        self.assertFalse(response.context['enabled'])
        self.assertFalse(response.context['welcome_enabled'])
        with self.assertRaises(HTTPException) as error:
            await save_verification(self.request, 'true', 'button', 'token')
        self.assertEqual(error.exception.status_code, 409)

    async def test_failure_actions_save_render_and_validation(self):
        result = await save_verification(self.request, 'true', 'arithmetic', 'token', '90', 'restrict', 'kick')
        self.assertEqual(result['timeout_action'], 'restrict')
        self.assertEqual(result['wrong_action'], 'kick')
        settings = await VerificationSettings.get(id=1)
        self.assertEqual((settings.timeout_action, settings.wrong_action), ('restrict', 'kick'))
        html = (await verification_page(self.request)).body.decode()
        self.assertIn('name="timeout_action"', html)
        self.assertIn('name="wrong_action"', html)
        for timeout_action, wrong_action in [('retry', 'kick'), ('kick', 'invalid')]:
            with self.assertRaises(HTTPException) as error:
                await save_verification(self.request, 'true', 'button', 'token', '60', timeout_action, wrong_action)
            self.assertEqual(error.exception.status_code, 400)

    async def test_failure_action_migration_keeps_existing_settings(self):
        from dao.database import migrate_join_actions
        await save_verification(self.request, 'false', 'arithmetic', 'token', '120', 'restrict', 'kick')
        await migrate_join_actions()
        await migrate_join_actions()
        settings = await VerificationSettings.get(id=1)
        self.assertEqual((settings.timeout_seconds, settings.timeout_action, settings.wrong_action), (120, 'restrict', 'kick'))

    async def test_wrong_action_none_and_legacy_migration(self):
        from dao.database import migrate_join_actions
        result = await save_verification(self.request, 'true', 'arithmetic', 'token', '60', 'kick', 'none')
        self.assertEqual(result['wrong_action'], 'none')
        html = (await verification_page(self.request)).body.decode()
        self.assertIn('value="none" selected>无</option>', html)
        self.assertNotIn('允许重新作答', html)
        await VerificationSettings.all().update(wrong_action='retry')
        await migrate_join_actions()
        self.assertEqual((await VerificationSettings.get()).wrong_action, 'none')
        await VerificationSettings.all().update(wrong_action='restrict')
        await migrate_join_actions()
        self.assertEqual((await VerificationSettings.get()).wrong_action, 'restrict')
        with self.assertRaises(HTTPException):
            await save_verification(self.request, 'true', 'arithmetic', 'token', '60', 'kick', 'retry')
