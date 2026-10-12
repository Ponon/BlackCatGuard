"""模拟时间与发送，不调用真实 Telegram。"""
import unittest
from datetime import datetime
from unittest.mock import AsyncMock, patch, ANY

from tortoise import Tortoise
from dao.models import BroadcastContent, BroadcastTask, BroadcastSchedule
from services.broadcast_scheduler import tick, next_due
from utils.dates import BEIJING_TIME


class SchedulerTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        await Tortoise.init(db_url='sqlite://:memory:', modules={'models': ['dao.models']})
        await Tortoise.generate_schemas()
        self.content = await BroadcastContent.create(name='公告', message='测试')
        self.now = datetime(2026, 10, 12, 8, 59, tzinfo=BEIJING_TIME).timestamp()

    async def asyncTearDown(self):
        await Tortoise.close_connections()

    async def test_daily_claim_once_and_multiple_times(self):
        task = await BroadcastTask.create(content=self.content, enabled=True, times=['09:00', '10:00'])
        with patch('services.broadcast_scheduler.deliver_broadcast', new_callable=AsyncMock) as send:
            await tick(self.now)
            await tick(self.now + 60)
            await tick(self.now + 60)
            send.assert_awaited_once_with(task.id, attempt_id=ANY)
            await tick(self.now + 3660)
            self.assertEqual(send.await_count, 2)

    async def test_interval_disabled_and_changed_rule(self):
        task = await BroadcastTask.create(content=self.content, enabled=True, schedule_mode='interval', interval_minutes=2)
        with patch('services.broadcast_scheduler.deliver_broadcast', new_callable=AsyncMock) as send:
            await tick(self.now)
            await tick(self.now + 119)
            send.assert_not_awaited()
            await tick(self.now + 120)
            send.assert_awaited_once()
            await BroadcastTask.filter(id=task.id).update(interval_minutes=3)
            await tick(self.now + 121)
            progress = await BroadcastSchedule.get(task_id=task.id)
            self.assertEqual(progress.next_due, self.now + 301)
            await BroadcastTask.filter(id=task.id).update(enabled=False)
            await tick(self.now + 301)
            self.assertEqual(send.await_count, 1)
            self.assertEqual(await BroadcastSchedule.all().count(), 0)

    async def test_missed_time_not_replayed_and_deleted_task_removed(self):
        task = await BroadcastTask.create(content=self.content, enabled=True, times=['09:00'])
        with patch('services.broadcast_scheduler.deliver_broadcast', new_callable=AsyncMock) as send:
            await tick(self.now)
            await tick(self.now + 180)
            send.assert_not_awaited()
            self.assertEqual((await BroadcastSchedule.get(task_id=task.id)).next_due, self.now + 86460)
            await task.delete()
            self.assertEqual(await BroadcastSchedule.all().count(), 0)

    async def test_daily_uses_beijing_and_rolls_to_tomorrow(self):
        task = await BroadcastTask.create(content=self.content, times=['09:00', '10:00'])
        self.assertEqual(next_due(task, self.now), self.now + 60)
        self.assertEqual(next_due(task, self.now + 3660), self.now + 86460)
