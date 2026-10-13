"""内容库、任务绑定和多时间点规则测试，不调用 Telegram。"""
import json
import unittest
from unittest.mock import patch, AsyncMock

from fastapi import HTTPException
from starlette.requests import Request
from tortoise import Tortoise

from dao.models import Administrator, BroadcastContent, BroadcastTask, BroadcastSettings, GroupConfig, EventLog, SystemSettings
from dao.database import migrate_broadcast_settings, migrate_daily_broadcast_tasks
from main import app
from routers.broadcast import broadcast_page, save_content, delete_content, save_task, delete_task, toggle_task, validate_buttons
from routers.group import save_config
from services.broadcast_delivery import deliver_broadcast
from services.telegram import TelegramError


class BroadcastTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        await Tortoise.init(db_url='sqlite://:memory:', modules={'models': ['dao.models']})
        await Tortoise.generate_schemas()
        await Administrator.create(id=1, username='admin', password_hash='test')
        self.request = Request({'type': 'http', 'method': 'POST', 'path': '/broadcast', 'headers': [], 'session': {'admin_id': 1, 'csrf_token': 'token'}, 'router': app.router, 'scheme': 'http', 'server': ('localhost', 80), 'query_string': b''})

    async def asyncTearDown(self):
        await Tortoise.close_connections()

    async def content(self, **values):
        fields = {'name': '群公告', 'message': '公告 {datetime}', 'csrf_token': 'token'}
        fields.update(values)
        return await save_content(self.request, **fields)

    async def task(self, content_id, **values):
        fields = {'content_id': content_id, 'csrf_token': 'token', 'times': '09:00'}
        enabled = values.pop('enabled', None)
        fields.update(values)
        result = await save_task(self.request, **fields)
        if enabled is not None:
            return await toggle_task(self.request, result['task']['id'], enabled, fields['csrf_token'])
        return result

    async def test_page_editor_has_no_switch(self):
        html = (await broadcast_page(self.request)).body.decode()
        content_form = html.split('id="broadcast-content-form"', 1)[1].split('</form>', 1)[0]
        task_form = html.split('id="broadcast-task-form"', 1)[1].split('</form>', 1)[0]
        self.assertNotIn('name="enabled"', content_form)
        self.assertNotIn('name="enabled"', task_form)
        self.assertNotIn('name="name"', task_form)
        self.assertNotIn('任务名称', task_form)
        self.assertLess(html.index('href="/antispam"'), html.index('href="/broadcast"'))
        self.assertIn('多个时间', html)
        self.assertNotIn('每周定时', html)

    async def test_multiple_contents_and_tasks_share_one_group(self):
        first = (await self.content())['content']
        second = (await self.content(name='活动提醒', message='活动通知'))['content']
        result = await self.task(first['id'], times='02:00,01:00,02:00')
        self.assertEqual(result['task']['times'], ['01:00', '02:00'])
        self.assertIn('01:00、02:00', result['task']['description'])
        await self.task(second['id'], schedule_mode='daily', times='10:00,18:00')
        self.assertEqual(await BroadcastContent.all().count(), 2)
        self.assertEqual(await BroadcastTask.all().count(), 2)
        await self.content(content_id=first['id'], message='更新公告')
        self.assertEqual((await BroadcastContent.get(id=first['id'])).message, '更新公告')
        self.assertEqual((await BroadcastTask.get(id=result['task']['id'])).content_id, first['id'])
        edited = await self.task(second['id'], task_id=result['task']['id'], times='03:00')
        self.assertEqual(edited['task']['content_id'], second['id'])
        self.assertEqual(edited['task']['times'], ['03:00'])
        self.assertEqual(await BroadcastTask.all().count(), 2)


    async def test_enable_gate_and_group_clear_preserves_content(self):
        content = (await self.content())['content']
        task = (await self.task(content['id']))['task']
        with self.assertRaises(HTTPException) as error:
            await toggle_task(self.request, task['id'], 'true', 'token')
        self.assertEqual(error.exception.status_code, 409)
        await GroupConfig.create(id=1, bot_token='test', chat_id='-1001', revision='test')
        await toggle_task(self.request, task['id'], 'true', 'token')
        await self.task(content['id'], enabled='true')
        await save_config(self.request, '', '', 'token')
        self.assertEqual(await BroadcastTask.filter(enabled=True).count(), 0)
        self.assertEqual(await BroadcastContent.all().count(), 1)
        self.assertEqual(await BroadcastTask.all().count(), 2)

    async def test_binding_protects_content_deletion(self):
        content = (await self.content())['content']
        task = (await self.task(content['id']))['task']
        with self.assertRaises(HTTPException) as error:
            await delete_content(self.request, content['id'], 'token')
        self.assertEqual(error.exception.status_code, 409)
        await delete_task(self.request, task['id'], 'token')
        await delete_content(self.request, content['id'], 'token')
        self.assertEqual(await BroadcastContent.all().count(), 0)
        with self.assertRaises(HTTPException):
            await self.task(content['id'])

    async def test_clearing_daily_times_does_not_overwrite_task(self):
        content = (await self.content())['content']
        task = (await self.task(content['id']))['task']
        for values in [{}, {'times': ''}]:
            with self.assertRaises(HTTPException) as error:
                await save_task(self.request, task_id=task['id'], content_id=content['id'], csrf_token='token', **values)
            self.assertEqual(error.exception.status_code, 400)
            self.assertEqual((await BroadcastTask.get(id=task['id'])).times, ['09:00'])

    async def test_invalid_rules_do_not_save(self):
        content = (await self.content())['content']
        for values in [
            {'schedule_mode': 'wrong'},
            {'times': ''}, {'times': '24:00'}, {'times': '1:00'},
            {'schedule_mode': 'weekly'},
            {'schedule_mode': 'interval', 'interval_minutes': '0'},
            {'schedule_mode': 'interval', 'interval_minutes': '1.5'},
            {'content_id': 999}, {'csrf_token': 'bad'},
        ]:
            values = dict(values)
            target = values.pop('content_id', content['id'])
            with self.subTest(values=values):
                with self.assertRaises(HTTPException):
                    await self.task(target, **values)
        self.assertEqual(await BroadcastTask.all().count(), 0)
        with self.assertRaises(HTTPException):
            await self.content(message='')

    async def test_safe_rendering_and_authentication(self):
        await self.content(message='<script>alert(1)</script>')
        html = (await broadcast_page(self.request)).body.decode()
        self.assertNotIn('<script>alert(1)</script>', html)
        self.assertIn('\\u003cscript\\u003e', html)
        self.request.session.clear()
        self.assertEqual((await broadcast_page(self.request)).headers['location'], '/login')
        with self.assertRaises(HTTPException) as error:
            await self.content()
        self.assertEqual(error.exception.status_code, 401)

    async def test_legacy_migration_preserves_settings_once(self):
        await GroupConfig.create(id=1, bot_token='test', chat_id='-1001', revision='test')
        await BroadcastSettings.create(id=1, enabled=True, message='旧公告', schedule_mode='weekly', send_time='02:00', weekdays=[0, 4])
        await migrate_broadcast_settings()
        await migrate_daily_broadcast_tasks()
        await migrate_broadcast_settings()
        await migrate_daily_broadcast_tasks()
        content = await BroadcastContent.get()
        task = await BroadcastTask.get()
        self.assertEqual(content.message, '旧公告')
        self.assertEqual(task.content_id, content.id)
        self.assertEqual(task.times, ['02:00'])
        self.assertEqual(task.weekdays, [0, 4])
        self.assertFalse(task.enabled)
        self.assertEqual(task.schedule_mode, 'daily')
        self.assertEqual(await BroadcastSettings.all().count(), 0)

    async def test_delivery_records_real_content_sender_and_proxy(self):
        await GroupConfig.create(id=1, bot_token='secret', chat_id='-1001', revision='test')
        await SystemSettings.create(id=1, proxy_url='http://localhost:7897')
        content = (await self.content())['content']
        task = (await self.task(content['id'], enabled='true'))['task']
        with patch('services.broadcast_delivery.execution_allowed', new_callable=AsyncMock, return_value=True), patch('services.broadcast_delivery.call_api', return_value={'message_id': 42, 'from': {'id': 456, 'first_name': '群助手', 'username': 'helper_bot'}}) as sender:
            result = await deliver_broadcast(task['id'])
        self.assertTrue(result['ok'])
        self.assertEqual(sender.call_args.args[1], 'sendMessage')
        self.assertEqual(sender.call_args.kwargs['proxy_url'], 'http://localhost:7897')
        record = await EventLog.get()
        self.assertEqual(record.event_type, '群广播')
        self.assertEqual(record.result, '发送成功')
        self.assertEqual(record.user_id, 456)
        self.assertEqual(record.nickname, '群助手')
        self.assertNotIn('{datetime}', record.content)
        self.assertNotIn('secret', record.result)
        self.assertEqual(record.content, sender.call_args.kwargs['text'])

    async def test_delivery_failure_and_disabled_skip(self):
        await GroupConfig.create(id=1, bot_token='secret', chat_id='-1001', revision='test')
        content = (await self.content())['content']
        task = (await self.task(content['id'], enabled='true'))['task']
        with patch('services.broadcast_delivery.execution_allowed', new_callable=AsyncMock, return_value=True), patch('services.broadcast_delivery.call_api', side_effect=TelegramError('连接失败')):
            result = await deliver_broadcast(task['id'])
        self.assertFalse(result['ok'])
        self.assertEqual((await EventLog.get()).result, '发送失败：连接失败')
        await BroadcastTask.filter(id=task['id']).update(enabled=False)
        with patch('services.broadcast_delivery.call_api') as sender:
            self.assertIsNone(await deliver_broadcast(task['id']))
            sender.assert_not_called()
        self.assertEqual(await EventLog.all().count(), 1)

    async def test_toggle_preserves_rules_and_edit_preserves_status(self):
        content = (await self.content())['content']
        result = await self.task(content['id'], times='01:00,02:00')
        task_id = result['task']['id']
        self.assertFalse(result['task']['enabled'])
        await GroupConfig.create(id=1, bot_token='test', chat_id='-1001', revision='test')
        await toggle_task(self.request, task_id, 'true', 'token')
        result = await self.task(content['id'], task_id=task_id, times='03:00')
        self.assertTrue(result['task']['enabled'])
        await toggle_task(self.request, task_id, 'false', 'token')
        task = await BroadcastTask.get(id=task_id)
        self.assertFalse(task.enabled)
        self.assertEqual(task.times, ['03:00'])
        for enabled, csrf in [('wrong', 'token'), ('true', 'bad')]:
            with self.assertRaises(HTTPException):
                await toggle_task(self.request, task_id, enabled, csrf)


class ButtonValidationTests(unittest.TestCase):
    def test_invalid_links_and_layout(self):
        for button in [
            {'text': '详情', 'url': 'javascript:alert(1)', 'row': 1},
            {'text': '详情', 'url': 'https://example.test', 'row': True},
            {'text': '', 'url': 'https://example.test', 'row': 1},
        ]:
            with self.assertRaises(HTTPException):
                validate_buttons(json.dumps([button]))
        with self.assertRaises(HTTPException):
            validate_buttons(json.dumps([{'text': '详情', 'url': 'https://example.test', 'row': 1}] * 9))
