"""发送重试与结果未知的持久化验证。"""
import time
import unittest
from unittest.mock import patch, AsyncMock
from tortoise import Tortoise
from dao.models import GroupConfig, BroadcastContent, BroadcastTask, BroadcastAttempt, EventLog
from services.broadcast_delivery import deliver_broadcast
from services.broadcast_scheduler import tick
from services.telegram import TelegramError


class DeliveryResilienceTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        await Tortoise.init(db_url='sqlite://:memory:', modules={'models': ['dao.models']})
        await Tortoise.generate_schemas()
        await GroupConfig.create(id=1, bot_token='fake', chat_id='-1001', revision='r1')
        content = await BroadcastContent.create(name='公告', message='正文')
        self.task = await BroadcastTask.create(content=content, enabled=True)
        self.gate = patch('services.broadcast_delivery.execution_allowed', new_callable=AsyncMock, return_value=True)
        self.gate.start()

    async def asyncTearDown(self):
        self.gate.stop()
        await Tortoise.close_connections()

    async def test_safe_retry_then_success(self):
        with patch('services.broadcast_delivery.call_api', side_effect=[TelegramError('连接被拒绝', retryable=True), {'message_id': 1}]) as sender, patch('services.broadcast_delivery.asyncio.sleep', new_callable=AsyncMock):
            result = await deliver_broadcast(self.task.id)
        self.assertTrue(result['ok'])
        self.assertEqual(sender.call_count, 2)
        self.assertEqual((await BroadcastAttempt.get()).state, 'success')
        self.assertIn('共尝试 2 次', (await EventLog.get()).result)

    async def test_timeout_not_retried_or_replayed(self):
        await BroadcastAttempt.create(id='slot', task_id=self.task.id, state='queued', started_at=time.time())
        with patch('services.broadcast_delivery.call_api', side_effect=TelegramError('超时', uncertain=True)) as sender:
            result = await deliver_broadcast(self.task.id, 'slot')
            await deliver_broadcast(self.task.id, 'slot')
        self.assertFalse(result['ok'])
        self.assertEqual(sender.call_count, 1)
        self.assertEqual((await BroadcastAttempt.get()).state, 'unknown')
        self.assertIn('未重发', (await EventLog.get()).result)

    async def test_disabled_during_retry_stops(self):
        async def stop(delay):
            await BroadcastTask.all().update(enabled=False)
        with patch('services.broadcast_delivery.call_api', side_effect=TelegramError('限流', retryable=True, retry_after=1)) as sender, patch('services.broadcast_delivery.asyncio.sleep', side_effect=stop):
            result = await deliver_broadcast(self.task.id)
        self.assertEqual(sender.call_count, 1)
        self.assertFalse(result['ok'])

    async def test_interrupted_attempt_logged_once_without_resend(self):
        await BroadcastTask.all().update(enabled=False)
        await BroadcastAttempt.create(id='crash', task_id=self.task.id, state='sending', started_at=time.time() - 180, content='正文')
        with patch('services.broadcast_scheduler.deliver_broadcast', new_callable=AsyncMock) as sender:
            await tick()
            await tick()
        sender.assert_not_awaited()
        self.assertEqual(await EventLog.all().count(), 1)
        self.assertIn('结果不确定', (await EventLog.get()).result)

    async def test_health_gate_blocks_send(self):
        with patch('services.broadcast_delivery.execution_allowed', new_callable=AsyncMock, return_value=False), patch('services.broadcast_delivery.call_api') as sender:
            result = await deliver_broadcast(self.task.id)
        sender.assert_not_called()
        self.assertFalse(result['ok'])
