"""Bark 设置与通知队列测试，全部模拟网络请求。"""
import io
import json
import time
import unittest
from unittest.mock import patch
from fastapi import HTTPException
from starlette.requests import Request
from tortoise import Tortoise
from main import app
from dao.models import Administrator, PushSettings, PushJob, EventLog, SystemSettings
from routers.settings import save_push_settings, test_push, settings_page
from services.notifications import push_tick, send_bark, PushError


class PushTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        await Tortoise.init(db_url='sqlite://:memory:', modules={'models': ['dao.models']})
        await Tortoise.generate_schemas()
        await Administrator.create(id=1, username='admin', password_hash='fake')
        self.request = Request({'type': 'http', 'method': 'POST', 'path': '/settings/push', 'headers': [], 'session': {'admin_id': 1, 'csrf_token': 'token'}, 'router': app.router, 'scheme': 'http', 'server': ('localhost', 80), 'query_string': b''})

    async def asyncTearDown(self):
        await Tortoise.close_connections()

    async def settings(self, provider='bark', events=None):
        return await save_push_settings(self.request, provider, 'https://example.invalid/device-key', events if events is not None else ['runtime_failed'], 'token')

    async def event(self):
        return await EventLog.create(event_type='自动停用', content='连续三轮检测失败', result='已关闭全部任务')

    async def test_runtime_subscription_covers_retry_exhaustion(self):
        await self.settings(events=['runtime_failed'])
        event = await EventLog.create(event_type='运行异常', content='超过最大重试次数', result='已关闭 入群验证 恢复后需手动开启')
        self.assertEqual(await PushJob.filter(event_id=event.id).count(), 1)

    async def test_old_auto_disabled_subscription_migrates(self):
        from dao.database import migrate_push_types
        await self.settings(events=['runtime_failed'])
        await PushSettings.all().update(events=['auto_disabled', 'runtime_failed'])
        await migrate_push_types()
        await migrate_push_types()
        self.assertEqual((await PushSettings.get()).events, ['runtime_failed'])

    async def test_push_log_names_source_event_instead_of_product_title(self):
        await self.settings(events=['runtime_failed'])
        await EventLog.create(event_type='运行异常', content='超过最大重试次数', result='当前没有已启用功能 无需停用')
        with patch('services.notifications.send_bark', side_effect=PushError('地址无法连接')):
            await push_tick()
        log = await EventLog.get(event_type='系统推送')
        self.assertEqual(log.content, '推送运行异常')
        self.assertEqual(log.result, '推送失败 地址无法连接')

    async def test_slow_push_keeps_reserved_row_next_to_source(self):
        await self.settings(events=['connection_failed'])
        source = await EventLog.create(event_type='连接异常', content='连接 Telegram 失败', result='连接 Telegram 中断，准备检测连接')
        placeholder = await EventLog.get(event_type='系统推送')
        self.assertEqual(placeholder.result, '推送中')
        timestamp = placeholder.created_at
        for _ in range(3):
            await EventLog.create(event_type='连接检测', content='Telegram连接重试', result='连接失败')
        with patch('services.notifications.send_bark'):
            await push_tick()
        rows = await EventLog.all().order_by('id')
        self.assertEqual([r.id for r in rows[:2]], [source.id, placeholder.id])
        self.assertEqual(rows[1].result, '推送成功')
        self.assertEqual(rows[1].created_at, timestamp)
        self.assertEqual(rows[1].parent_event_id, source.id)
        self.assertEqual(rows[1].flow_id, source.flow_id)
        self.assertEqual(len(rows), 5)

    async def test_broadcast_success_notification_and_option_order(self):
        await self.settings(events=['broadcast_success'])
        event = await EventLog.create(event_type='群广播', content='群公告', result='发送成功 共尝试 2 次')
        job = await PushJob.get(event_id=event.id)
        self.assertEqual(job.event_key, 'broadcast_success')
        with patch('services.notifications.send_bark'):
            await push_tick()
        log = await EventLog.get(event_type='系统推送')
        self.assertEqual(log.content, '推送广播成功')
        self.assertEqual(log.result, '推送成功')
        html = (await settings_page(self.request)).body.decode()
        self.assertIn('推送类型', html)
        self.assertLess(html.index('push-event-broadcast_success'), html.index('push-event-broadcast_failed'))
        self.assertNotIn('功能自动关闭', html)
        self.assertNotIn('push-event-auto_disabled', html)

    async def test_skipped_broadcast_is_not_a_delivery_failure(self):
        await self.settings(events=['broadcast_failed'])
        await EventLog.create(event_type='群广播', content='任务 1', result='任务已关闭或删除 本次计划已跳过')
        self.assertEqual(await PushJob.all().count(), 0)
        await EventLog.create(event_type='群广播', content='正文', result='发送失败 网络异常')
        self.assertEqual(await PushJob.all().count(), 1)

    async def test_runtime_error_matches_runtime_subscription(self):
        await self.settings(events=['runtime_failed'])
        event = await EventLog.create(event_type='运行异常', content='接收或处理 Telegram 事件失败', result='连接 Telegram 中断')
        self.assertEqual((await PushJob.get(event_id=event.id)).event_key, 'runtime_failed')

    async def test_detection_logs_never_notify_until_recovery(self):
        await self.settings(events=['runtime_failed', 'connection_failed', 'connection_restored'])
        for _ in range(3):
            await EventLog.create(event_type='连接检测', content='Telegram连接重试', result='连接失败')
        self.assertEqual(await PushJob.all().count(), 0)
        await EventLog.create(event_type='连接检测', content='Telegram连接重试', result='连接成功')
        self.assertEqual(await PushJob.all().count(), 0)
        await EventLog.create(event_type='连接恢复', content='Telegram 连接恢复正常', result='连接已恢复 功能保持当前开关状态')
        self.assertEqual(await PushJob.all().count(), 1)

    async def test_selection_only_new_events_and_push_failure_cannot_recurse(self):
        await self.event()
        await self.settings()
        self.assertEqual(await PushJob.all().count(), 0)
        await EventLog.create(event_type='广告拦截', content='广告', result='垃圾消息 已删除')
        self.assertEqual(await PushJob.all().count(), 0)
        await self.event()
        self.assertEqual(await PushJob.all().count(), 1)
        with patch('services.notifications.send_bark', side_effect=PushError('超时', uncertain=True)) as sender:
            await push_tick()
            await push_tick()
        self.assertEqual(sender.call_count, 1)
        self.assertEqual((await PushJob.get()).state, 'unknown')
        self.assertEqual(await EventLog.filter(event_type='系统推送').count(), 1)

    async def test_disabled_or_changed_settings_cancel_queued_notifications(self):
        await self.settings()
        await self.event()
        await self.settings(provider='')
        with patch('services.notifications.send_bark') as sender:
            await push_tick()
        sender.assert_not_called()
        self.assertEqual((await PushJob.get()).state, 'cancelled')

    async def test_success_bypasses_configured_proxy_and_is_not_sent_twice(self):
        await self.settings()
        await SystemSettings.create(id=1, proxy_url='http://localhost:7897')
        await self.event()
        with patch('services.notifications.send_bark') as sender:
            await push_tick()
            await push_tick()
        self.assertEqual(sender.call_count, 1)
        self.assertEqual(len(sender.call_args.args), 3)
        self.assertEqual((await PushJob.get()).state, 'done')

    async def test_safe_network_error_retries_at_most_three_times(self):
        await self.settings()
        await self.event()
        with patch('services.notifications.send_bark', side_effect=PushError('连接失败', retryable=True)) as sender:
            for _ in range(4):
                await PushJob.all().update(available_at=0)
                await push_tick()
        self.assertEqual(sender.call_count, 3)
        self.assertEqual((await PushJob.get()).state, 'failed')

    async def test_test_message_works_without_enabled_push(self):
        await self.settings(provider='')
        with patch('routers.settings.send_bark') as sender:
            response = await test_push(self.request, 'token')
        self.assertEqual(sender.call_count, 1)
        self.assertIn('已接收', response['message'])
        html = (await settings_page(self.request)).body.decode()
        self.assertIn('系统推送', html)
        self.assertIn('type="checkbox" name="events"', html)
        self.assertIn('>Bark</option>', html)

    async def test_invalid_settings_and_csrf_rejected(self):
        for provider, url, events, csrf in [
            ('unknown', '', [], 'token'), ('bark', 'file:///secret', ['ad_block'], 'token'),
            ('bark', 'https://example.invalid', ['ad_block'], 'token'),
            ('bark', 'https://example.invalid/key', ['unknown'], 'token'),
            ('bark', 'https://example.invalid/key', [], 'token'),
            ('bark', 'https://example.invalid/key', ['ad_block'], 'bad'),
        ]:
            with self.assertRaises(HTTPException):
                await save_push_settings(self.request, provider, url, events, csrf)
        self.assertEqual(await PushSettings.all().count(), 0)

    async def test_interrupted_delivery_not_repeated(self):
        await self.settings()
        await self.event()
        await PushJob.all().update(state='sending', started_at=time.time() - 180)
        with patch('services.notifications.send_bark') as sender:
            await push_tick()
        sender.assert_not_called()
        self.assertEqual((await PushJob.get()).state, 'unknown')


    async def test_wrong_answer_notification_can_be_selected(self):
        await self.settings(events=['verification_wrong'])
        await EventLog.create(event_type='入群验证', content='算术验证', result='回答错误 已封禁用户所有权限')
        job = await PushJob.get()
        self.assertEqual(job.event_key, 'verification_wrong')
        self.assertEqual(job.title, '黑猫群管')
        self.assertIn('验证答错', job.body)


class NotificationBodyTests(unittest.TestCase):
    def test_all_event_summaries_are_short_single_lines(self):
        from types import SimpleNamespace
        from services.notifications import EVENT_OPTIONS, notification_body
        event = SimpleNamespace(nickname='小黑\n@blackcat', username='', content='长消息\n' * 300,
                                result='回答错误 已限制用户权限\n' * 100)
        for key in EVENT_OPTIONS:
            body = notification_body(event, key)
            self.assertNotIn('\n', body)
            self.assertNotIn('\r', body)
            self.assertLessEqual(len(body), 160)
        event.content = '超过最大重试次数'
        event.result = '当前没有已启用功能 无需停用'
        self.assertEqual(notification_body(event, 'runtime_failed'),
                         'Telegram 连续重试 3 次失败，当前没有已启用功能 无需停用')
        self.assertEqual(notification_body(event, 'broadcast_success'), '群广播发送成功')


class BarkTransportTests(unittest.TestCase):
    def test_direct_transport_ignores_environment_proxy(self):
        from services.network import open_request
        from urllib.request import Request
        with patch.dict('os.environ', {'HTTPS_PROXY': 'http://localhost:1'}), patch('services.network.build_opener') as build:
            open_request(Request('https://example.invalid'), direct=True)
        self.assertEqual(build.call_args.args[0].proxies, {})


    def test_post_json_and_validate_success(self):
        response = io.BytesIO(json.dumps({'code': 200}).encode())
        with patch('services.notifications.open_request', return_value=response) as opener:
            send_bark('https://example.invalid/key', '标题', '正文')
        request = opener.call_args.args[0]
        self.assertEqual(request.method, 'POST')
        self.assertEqual(json.loads(request.data)['body'], '正文')
        self.assertEqual(json.loads(request.data)['group'], '黑猫群管')
        self.assertEqual(json.loads(request.data)['title'], '黑猫群管')
        self.assertTrue(opener.call_args.kwargs['direct'])

    def test_error_does_not_expose_device_key(self):
        with patch('services.notifications.open_request', side_effect=TimeoutError()), self.assertRaises(PushError) as error:
            send_bark('https://example.invalid/secret-key', '标题', '正文')
        self.assertTrue(error.exception.uncertain)
        self.assertNotIn('secret-key', str(error.exception))
