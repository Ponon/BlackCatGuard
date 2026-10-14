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
        from services.runtime_health import report_connection_fault
        from dao.models import ReceiverFault
        if not ok or await ReceiverFault.exists():
            await report_connection_fault(self.group, '', '模拟连接中断')
        await RuntimeHealth.all().update(checked_at=time.time() - 11)
        with patch('services.runtime_health.inspect_group', return_value={'ok': ok, 'message': '模拟代理连接失败'}):
            await probe_once()

    async def test_three_failures_pause_once_and_recovery_resumes_without_changing_switches(self):
        await self.probe(True)
        self.assertTrue(await execution_allowed())
        for index in (1, 2):
            await self.probe(False)
            self.assertTrue((await BroadcastTask.get()).enabled)
            self.assertEqual((await RuntimeHealth.get()).failures, index)
            self.assertTrue(await execution_allowed())
        await self.probe(False)
        for model in (VerificationSettings, WelcomeSettings, KeywordSettings, AISettings):
            self.assertTrue((await model.get()).enabled)
        self.assertTrue((await BroadcastTask.get()).enabled)
        self.assertFalse(await execution_allowed())
        with self.assertRaises(HTTPException):
            await ensure_can_enable()
        await self.probe(False)
        self.assertEqual(await EventLog.filter(result__startswith='已暂停 ').count(), 1)
        await self.probe(True)
        self.assertEqual((await health_snapshot())['state'], 'online')
        self.assertTrue((await BroadcastTask.get()).enabled)
        self.assertTrue(await EventLog.filter(result__contains='已自动恢复').exists())
        self.assertTrue(await execution_allowed())

    async def test_no_enabled_features_does_not_log_auto_disable(self):
        for model in (VerificationSettings, WelcomeSettings, KeywordSettings, AISettings, BroadcastTask):
            await model.all().update(enabled=False)
        for _ in range(3):
            await self.probe(False)
        self.assertFalse(await EventLog.filter(event_type='自动停用').exists())
        self.assertTrue(await EventLog.filter(result__contains='没有已启用功能').exists())
        await self.probe(True)
        recovered = await EventLog.filter(result='连接成功').get()
        self.assertNotIn('已关闭', recovered.result)

    async def test_pause_only_names_enabled_features(self):
        for model in (WelcomeSettings, KeywordSettings, AISettings, BroadcastTask):
            await model.all().update(enabled=False)
        for _ in range(3):
            await self.probe(False)
        log = await EventLog.filter(result__startswith='已暂停 ').get()
        self.assertIn('入群验证', log.result)
        for name in ('欢迎语', '关键词', 'AI', '广播'):
            self.assertNotIn(name, log.result)

    async def test_failure_series_has_consistent_type_and_content(self):
        for _ in range(3):
            await self.probe(False)
        rows = await EventLog.all().order_by('id')
        self.assertEqual([row.event_type for row in rows], ['连接异常', '连接检测', '连接检测', '连接检测', '运行异常'])
        self.assertEqual(rows[0].content, '连接 Telegram 失败')
        self.assertEqual(rows[0].result, '连接 Telegram 中断，准备检测连接')
        self.assertEqual([row.content for row in rows[1:4]], ['Telegram连接重试'] * 3)
        self.assertEqual([row.result for row in rows[1:4]], ['连接失败'] * 3)
        self.assertEqual(rows[4].content, '超过最大重试次数')

    async def test_full_outage_log_sequence_with_push_results(self):
        from dao.models import PushSettings
        from services.notifications import push_tick
        from services.runtime_health import report_connection_fault
        for model in (VerificationSettings, WelcomeSettings, KeywordSettings, AISettings, BroadcastTask):
            await model.all().update(enabled=False)
        await PushSettings.create(id=1, provider='bark', bark_url='https://example.invalid/key',
                                  events=['connection_failed', 'runtime_failed'], revision='push')
        with patch('services.notifications.send_bark') as sender:
            await report_connection_fault(self.group, '', '首次中断')
            await report_connection_fault(self.group, '', '随后超时')
            await push_tick()
            for _ in range(3):
                await self.probe(False)
                await push_tick()
            await self.probe(False)
            await push_tick()
        self.assertEqual(sender.call_count, 2)
        rows = await EventLog.all().order_by('id')
        self.assertEqual([(r.event_type, r.content, r.result) for r in rows], [
            ('连接异常', '连接 Telegram 失败', '连接 Telegram 中断，准备检测连接'),
            ('系统推送', '推送连接异常', '推送成功'),
            ('连接检测', 'Telegram连接重试', '连接失败'),
            ('连接检测', 'Telegram连接重试', '连接失败'),
            ('连接检测', 'Telegram连接重试', '连接失败'),
            ('运行异常', '超过最大重试次数', '当前没有已启用功能 无需暂停 继续检测连接'),
            ('系统推送', '推送运行异常', '推送成功'),
        ])
        self.assertEqual(len({r.flow_id for r in rows}), 1)
        self.assertTrue(rows[0].flow_id)
        for child, parent in ((1, 0), (2, 0), (3, 2), (4, 3), (5, 4), (6, 5)):
            self.assertEqual(rows[child].parent_event_id, rows[parent].id)
        await self.probe(True)
        recovered = await EventLog.all().order_by('-id').limit(2)
        self.assertEqual([(r.event_type, r.result) for r in reversed(recovered)], [
            ('连接检测', '连接成功'), ('连接恢复', '连接已恢复 当前没有已启用功能')])
        self.assertEqual(recovered[0].parent_event_id, recovered[1].id)
        self.assertEqual(recovered[0].flow_id, rows[0].flow_id)
        await report_connection_fault(self.group, '', '下一次中断')
        self.assertEqual(await EventLog.filter(event_type='连接异常').count(), 2)
        next_outage = await EventLog.filter(event_type='连接异常').order_by('-id').first()
        self.assertNotEqual(next_outage.flow_id, rows[0].flow_id)

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
        self.assertIn('接收程序冲突', (await RuntimeHealth.get()).reason)

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


    async def test_paused_switches_survive_and_manual_disable_is_respected(self):
        for _ in range(3):
            await self.probe(False)
        self.assertTrue((await RuntimeHealth.get()).stopped)
        self.assertTrue((await KeywordSettings.get()).enabled)
        await KeywordSettings.all().update(enabled=False)
        await BroadcastTask.filter(id=self.task.id).update(enabled=False)
        await self.probe(True)
        self.assertFalse((await RuntimeHealth.get()).stopped)
        self.assertTrue(await execution_allowed())
        self.assertFalse((await KeywordSettings.get()).enabled)
        self.assertFalse((await BroadcastTask.get()).enabled)
        self.assertTrue((await VerificationSettings.get()).enabled)
        recovered = await EventLog.get(event_type='连接恢复')
        self.assertIn('已自动恢复', recovered.result)
        self.assertNotIn('关键词拦截', recovered.result)
        self.assertNotIn('广播任务', recovered.result)

    async def test_recovery_discards_elapsed_broadcast_slots_and_keeps_future_slots(self):
        from dao.models import BroadcastSchedule
        from services.broadcast_scheduler import rule_key
        now = time.time()
        await BroadcastTask.filter(id=self.task.id).update(schedule_mode='interval', interval_minutes=2)
        task = await BroadcastTask.get(id=self.task.id)
        progress = await BroadcastSchedule.create(task_id=task.id, fingerprint=rule_key(task), next_due=now - 5)
        for _ in range(3):
            await self.probe(False)
        self.assertTrue(await BroadcastSchedule.filter(id=progress.id).exists())
        await self.probe(True)
        updated = await BroadcastSchedule.get(id=progress.id)
        self.assertGreater(updated.next_due, now + 119)
        self.assertTrue((await BroadcastTask.get()).enabled)

    async def test_repeated_failures_keep_configuration_and_only_one_pause_notice(self):
        await KeywordSettings.all().update(keywords=['保留配置'])
        for _ in range(6):
            await self.probe(False)
        self.assertEqual((await KeywordSettings.get()).keywords, ['保留配置'])
        self.assertTrue((await KeywordSettings.get()).enabled)
        self.assertEqual(await EventLog.filter(event_type='运行异常').count(), 1)
        self.assertEqual(await EventLog.filter(event_type='连接检测').count(), 3)
        self.assertFalse(await execution_allowed())


class RetryDelayTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        await Tortoise.init(db_url='sqlite://:memory:', modules={'models': ['dao.models']})
        await Tortoise.generate_schemas()
        self.group = await GroupConfig.create(id=1, bot_token='fake', chat_id='-1001', revision='r1')

    async def asyncTearDown(self):
        await Tortoise.close_connections()

    async def test_initial_failure_not_counted_and_each_retry_waits_ten_seconds(self):
        from services.runtime_health import record_probe
        result = {'ok': False, 'message': '连接中断'}
        with patch('services.runtime_health.time.time', return_value=100):
            await record_probe(self.group, '', result)
        self.assertEqual((await RuntimeHealth.get()).failures, 0)
        self.assertEqual(await EventLog.filter(event_type='连接检测').count(), 0)
        for now, count in ((109, 0), (110, 1), (119, 1), (120, 2), (129, 2), (130, 3)):
            with patch('services.runtime_health.time.time', return_value=now):
                await record_probe(self.group, '', result)
            self.assertEqual((await RuntimeHealth.get()).failures, count)
        self.assertEqual(await EventLog.filter(event_type='连接检测').count(), 3)
        self.assertEqual(await EventLog.filter(event_type='运行异常').count(), 1)

    async def test_receiver_fault_waits_before_starting_first_network_probe(self):
        from services.runtime_health import report_connection_fault
        with patch('services.runtime_health.time.time', return_value=100):
            await report_connection_fault(self.group, '', '接收中断')
        with patch('services.runtime_health.inspect_group', return_value={'ok': False}) as inspect:
            with patch('services.runtime_health.time.time', return_value=109):
                await probe_once()
            inspect.assert_not_called()
            with patch('services.runtime_health.time.time', return_value=110):
                await probe_once()
            inspect.assert_called_once()
        self.assertEqual((await RuntimeHealth.get()).failures, 1)
