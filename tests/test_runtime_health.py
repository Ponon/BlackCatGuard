"""健康监测、熔断和恢复使用模拟网络，不接触真实群。"""
import time
import unittest
from unittest.mock import patch
from tortoise import Tortoise
from fastapi import HTTPException
from dao.models import (GroupConfig, RuntimeHealth, VerificationSettings, WelcomeSettings, KeywordSettings,
                        AISettings, BroadcastTask, BroadcastContent, EventLog, SystemSettings)
from services.runtime_health import probe_once, health_snapshot, execution_allowed, ensure_can_enable


class HealthTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        await Tortoise.init(db_url='sqlite://:memory:', modules={'models': ['dao.models']})
        await Tortoise.generate_schemas()
        self.group = await GroupConfig.create(id=1, bot_token='fake', chat_id='-1001', revision='r1')
        for model in (VerificationSettings, WelcomeSettings, KeywordSettings, AISettings):
            await model.create(id=1, enabled=True)
        content = await BroadcastContent.create(name='公告', message='正文')
        self.task = await BroadcastTask.create(content=content, enabled=True)

    async def asyncTearDown(self):
        await Tortoise.close_connections()

    async def probe(self, ok):
        with patch('services.runtime_health.inspect_group', return_value={'ok': ok, 'message': '模拟代理连接失败'}):
            await probe_once()

    async def test_three_failures_close_everything_once_and_recovery_does_not_enable(self):
        await self.probe(True)
        self.assertTrue(await execution_allowed())
        for index in (1, 2):
            await self.probe(False)
            self.assertTrue((await BroadcastTask.get()).enabled)
            self.assertEqual((await RuntimeHealth.get()).failures, index)
            self.assertTrue(await execution_allowed())
        await self.probe(False)
        for model in (VerificationSettings, WelcomeSettings, KeywordSettings, AISettings):
            self.assertFalse((await model.get()).enabled)
        self.assertFalse((await BroadcastTask.get()).enabled)
        self.assertFalse(await execution_allowed())
        with self.assertRaises(HTTPException):
            await ensure_can_enable()
        await self.probe(False)
        self.assertEqual(await EventLog.filter(event_type='自动停用').count(), 1)
        await self.probe(True)
        self.assertEqual((await health_snapshot())['state'], 'online')
        self.assertFalse((await BroadcastTask.get()).enabled)
        self.assertTrue(await EventLog.filter(result__contains='需手动开启').exists())

    async def test_success_resets_failure_count(self):
        await self.probe(False)
        await self.probe(False)
        await self.probe(True)
        await self.probe(False)
        self.assertEqual((await RuntimeHealth.get()).failures, 1)
        self.assertTrue((await BroadcastTask.get()).enabled)

    async def test_stale_and_changed_proxy_cannot_execute(self):
        self.assertFalse(await execution_allowed())
        await self.probe(True)
        await RuntimeHealth.all().update(checked_at=time.time() - 60)
        self.assertEqual((await health_snapshot())['state'], 'stale')
        self.assertFalse(await execution_allowed())
        await self.probe(True)
        await SystemSettings.create(id=1, proxy_url='http://localhost:7897')
        self.assertFalse(await execution_allowed())
        self.assertEqual((await health_snapshot())['state'], 'checking')

    async def test_old_probe_cannot_overwrite_new_group(self):
        async def changed_probe(*args, **kwargs):
            await GroupConfig.filter(id=1).update(revision='r2')
            return {'ok': False, 'message': '旧结果'}
        with patch('services.runtime_health.run_in_threadpool', side_effect=changed_probe):
            await probe_once()
        self.assertEqual(await RuntimeHealth.all().count(), 0)
        self.assertTrue((await BroadcastTask.get()).enabled)

    async def test_receiver_fault_participates_in_health_checks(self):
        from dao.models import ReceiverFault
        from services.runtime_health import config_key
        await ReceiverFault.create(id=1, config_key=config_key(self.group, ''), reason='机器人接收程序冲突')
        for _ in range(3):
            await self.probe(True)
        self.assertTrue((await RuntimeHealth.get()).stopped)
        self.assertIn('接收程序冲突', (await EventLog.filter(event_type='自动停用').get()).content)

    async def test_status_endpoint_is_authenticated_and_no_store(self):
        from main import app
        from starlette.requests import Request
        from routers.pages import runtime_status
        from dao.models import Administrator
        request = Request({'type': 'http', 'method': 'GET', 'path': '/runtime/status', 'headers': [],
            'session': {}, 'router': app.router, 'scheme': 'http', 'server': ('localhost', 80), 'query_string': b''})
        with self.assertRaises(HTTPException) as error:
            await runtime_status(request)
        self.assertEqual(error.exception.status_code, 401)
        await Administrator.create(id=1, username='admin', password_hash='fake')
        request.session['admin_id'] = 1
        response = await runtime_status(request)
        self.assertEqual(response.headers['cache-control'], 'no-store')
        self.assertNotIn('bot_token', response.body.decode())

    async def test_old_group_snapshot_cannot_execute_after_config_changes(self):
        await self.probe(True)
        self.assertTrue(await execution_allowed(self.group))
        await GroupConfig.filter(id=1).update(chat_id='-1002', revision='r2')
        self.assertFalse(await execution_allowed(self.group))
        self.assertFalse(await execution_allowed())

    async def test_latency_colors_threshold_failure_and_stale_reset(self):
        for latency, tone in [(450, 'good'), (999, 'good'), (1000, 'slow'), (1800, 'slow')]:
            with patch('services.runtime_health.inspect_group', return_value={'ok': True, 'latency_ms': latency}):
                await probe_once()
            data = await health_snapshot()
            self.assertEqual(data['latency_ms'], latency)
            self.assertEqual(data['tone'], tone)
            self.assertEqual(data['latency_label'], f'{latency} ms')
        await self.probe(False)
        data = await health_snapshot()
        self.assertEqual(data['tone'], 'warning')
        self.assertIsNone(data['latency_ms'])
        with patch('services.runtime_health.inspect_group', return_value={'ok': True, 'latency_ms': 350}):
            await probe_once()
        await RuntimeHealth.all().update(checked_at=time.time() - 60)
        data = await health_snapshot()
        self.assertEqual(data['tone'], 'error')
        self.assertIsNone(data['latency_ms'])
        self.assertEqual(data['latency_label'], '—')

    async def test_latency_schema_migrates_without_fabricating_old_measurement(self):
        from tortoise import Tortoise
        from dao.database import migrate_runtime_latency
        await self.probe(True)
        connection = Tortoise.get_connection('default')
        await connection.execute_query('ALTER TABLE runtime_health DROP COLUMN latency_ms')
        await migrate_runtime_latency()
        await migrate_runtime_latency()
        self.assertIsNone((await RuntimeHealth.get()).latency_ms)
        self.assertEqual((await health_snapshot())['latency_label'], '—')
