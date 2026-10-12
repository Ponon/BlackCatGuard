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
        return await save_push_settings(self.request, provider, 'https://example.invalid/device-key', events if events is not None else ['auto_disabled'], 'token')

    async def event(self):
        return await EventLog.create(event_type='自动停用', content='连续三轮检测失败', result='已关闭全部任务')

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

    async def test_success_uses_proxy_and_is_not_sent_twice(self):
        await self.settings()
        await SystemSettings.create(id=1, proxy_url='http://localhost:7897')
        await self.event()
        with patch('services.notifications.send_bark') as sender:
            await push_tick()
            await push_tick()
        self.assertEqual(sender.call_count, 1)
        self.assertEqual(sender.call_args.args[-1], 'http://localhost:7897')
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
        self.assertIn('验证答错', job.title)


class BarkTransportTests(unittest.TestCase):
    def test_post_json_and_validate_success(self):
        response = io.BytesIO(json.dumps({'code': 200}).encode())
        with patch('services.notifications.open_request', return_value=response) as opener:
            send_bark('https://example.invalid/key', '标题', '正文', 'http://localhost:7897')
        request = opener.call_args.args[0]
        self.assertEqual(request.method, 'POST')
        self.assertEqual(json.loads(request.data)['body'], '正文')
        self.assertEqual(opener.call_args.kwargs['proxy_url'], 'http://localhost:7897')

    def test_error_does_not_expose_device_key(self):
        with patch('services.notifications.open_request', side_effect=TimeoutError()), self.assertRaises(PushError) as error:
            send_bark('https://example.invalid/secret-key', '标题', '正文')
        self.assertTrue(error.exception.uncertain)
        self.assertNotIn('secret-key', str(error.exception))
