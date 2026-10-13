"""保留期、永久保留、设置验证及旧数据库迁移"""
import unittest
from datetime import timedelta
from unittest.mock import patch
from starlette.requests import Request
from fastapi import HTTPException
from tortoise import Tortoise
from dao.models import Administrator, EventLog, SystemSettings, event_time_now
from dao.database import migrate_log_retention
from routers.settings import save_log_settings
from services.log_retention import cleanup_logs


class LogRetentionTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        await Tortoise.init(db_url='sqlite://:memory:', modules={'models': ['dao.models']}, use_tz=False, timezone='UTC')
        await Tortoise.generate_schemas()
        await Administrator.create(id=1, username='admin', password_hash='fake')
        self.request = Request({'type': 'http', 'session': {'admin_id': 1, 'csrf_token': 'token'}})

    async def asyncTearDown(self):
        await Tortoise.close_connections()

    async def test_default_thirty_days_and_boundary(self):
        now = event_time_now()
        old = await EventLog.create(event_type='测试', result='过期', created_at=now - timedelta(days=30, seconds=1))
        boundary = await EventLog.create(event_type='测试', result='保留', created_at=now - timedelta(days=30))
        with patch('services.log_retention.event_time_now', return_value=now):
            self.assertEqual(await cleanup_logs(), 1)
        self.assertFalse(await EventLog.filter(id=old.id).exists())
        self.assertTrue(await EventLog.filter(id=boundary.id).exists())

    async def test_zero_and_custom_days_preserve_proxy(self):
        await SystemSettings.create(id=1, proxy_url='http://localhost:9999')
        await EventLog.create(event_type='测试', result='旧日志', created_at=event_time_now() - timedelta(days=100))
        await save_log_settings(self.request, '0', 'token')
        self.assertEqual(await cleanup_logs(), 0)
        await save_log_settings(self.request, '7', 'token')
        self.assertEqual(await cleanup_logs(), 1)
        self.assertEqual((await SystemSettings.get(id=1)).proxy_url, 'http://localhost:9999')

    async def test_invalid_days_and_csrf_do_not_change_settings(self):
        for value in ('', '-1', '1.5', 'abc', '36501', '99999999999'):
            with self.assertRaises(HTTPException):
                await save_log_settings(self.request, value, 'token')
        with self.assertRaises(HTTPException):
            await save_log_settings(self.request, '1', 'wrong')
        self.assertIsNone(await SystemSettings.get_or_none(id=1))

    async def test_old_schema_migration_is_repeatable(self):
        db = Tortoise.get_connection('default')
        await db.execute_query('DROP TABLE system_settings')
        await db.execute_query('CREATE TABLE system_settings (id INTEGER PRIMARY KEY, proxy_url TEXT)')
        await db.execute_query("INSERT INTO system_settings VALUES (1, 'http://localhost:9999')")
        await migrate_log_retention()
        await migrate_log_retention()
        settings = await SystemSettings.get(id=1)
        self.assertEqual(settings.log_retention_days, 30)
        self.assertEqual(settings.proxy_url, 'http://localhost:9999')
