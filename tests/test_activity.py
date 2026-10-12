"""统计仅来自真实群事件，事件重放、编辑和清空日志不重复计数。"""
import time
import unittest
from unittest.mock import patch, AsyncMock
from datetime import datetime, timedelta
from tortoise import Tortoise
from dao.models import GroupConfig, VerificationSettings, KeywordSettings, EventLog, ModerationJob
from services.activity import record_activity, today_counts
from services.dashboard import build_dashboard
from services.join_runtime import process_join, bot_key
from services.moderation import enqueue_message, process_job
from utils.dates import BEIJING_TIME


class ActivityTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        await Tortoise.init(db_url='sqlite://:memory:', modules={'models': ['dao.models']})
        await Tortoise.generate_schemas()
        self.group = await GroupConfig.create(id=1, bot_token='fake', chat_id='-1001', revision='r1')
        self.key = bot_key('fake')

    async def asyncTearDown(self):
        await Tortoise.close_connections()

    async def test_today_scope_duplicate_and_clear_logs(self):
        await record_activity(self.key, '-1001', 'join', 1)
        await record_activity(self.key, '-1001', 'join', 1)
        await record_activity(self.key, '-1002', 'join', 2)
        await record_activity('other-bot', '-1001', 'join', 3)
        yesterday = datetime.now(BEIJING_TIME).replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(seconds=1)
        await record_activity(self.key, '-1001', 'join', 4, yesterday.timestamp())
        await record_activity(self.key, '-1001', 'blocked', 5)
        await EventLog.create(event_type='消息测试', result='垃圾消息 关键词命中')
        await EventLog.all().delete()
        self.assertEqual(await today_counts(self.key, '-1001'), (1, 1))
        data = await build_dashboard()
        self.assertEqual((data['members_today'], data['blocked_today']), (1, 1))

    async def test_join_count_does_not_require_verification_or_welcome(self):
        event = {'chat': {'id': -1001}, 'date': time.time(), 'old_chat_member': {'status': 'left'},
            'new_chat_member': {'status': 'member', 'user': {'id': 123}}}
        await process_join(self.group, event, 10)
        await process_join(self.group, event, 10)
        self.assertEqual(await today_counts(self.key, '-1001'), (1, 0))
        event['date'] -= 3600
        await process_join(self.group, event, 11)
        self.assertEqual(await today_counts(self.key, '-1001'), (1, 0))

    async def test_block_counts_only_executed_deletion_and_not_edits(self):
        await KeywordSettings.create(id=1, enabled=True, keywords=['广告'])
        message = {'chat': {'id': -1001}, 'message_id': 50, 'from': {'id': 123}, 'text': '广告'}
        with patch('services.moderation.execution_allowed', new_callable=AsyncMock, return_value=True), patch('services.moderation.api', new_callable=AsyncMock, return_value=True):
            for update in (1, 2):
                await enqueue_message(self.group, message, update)
                await process_job((await ModerationJob.get(update_id=update)).id)
        self.assertEqual(await today_counts(self.key, '-1001'), (0, 1))
        self.assertEqual((await build_dashboard())['blocked_today'], 1)
