"""广告处理使用模拟网络，覆盖配置变化与消息编辑。"""
import time
import unittest
from unittest.mock import patch, AsyncMock
from tortoise import Tortoise
from dao.models import GroupConfig, KeywordSettings, AISettings, WhitelistSettings, ModerationJob, EventLog
from services.moderation import enqueue_message, process_job, moderation_tick
from services.telegram import TelegramError


class ModerationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        await Tortoise.init(db_url='sqlite://:memory:', modules={'models': ['dao.models']})
        await Tortoise.generate_schemas()
        self.group = await GroupConfig.create(id=1, bot_token='fake', chat_id='-1001', revision='r1')
        await KeywordSettings.create(id=1, enabled=True, keywords=['广告'])
        self.message = {'message_id': 10, 'chat': {'id': -1001}, 'from': {'id': 123, 'first_name': '小猫', 'username': 'cat'}, 'text': '这是广告'}
        self.gate = patch('services.moderation.execution_allowed', new_callable=AsyncMock, return_value=True)
        self.gate.start()

    async def asyncTearDown(self):
        self.gate.stop()
        await Tortoise.close_connections()

    async def enqueue(self, update_id=1):
        await enqueue_message(self.group, self.message, update_id)
        return await ModerationJob.get(update_id=update_id)

    async def test_keyword_deletes_and_duplicate_update_is_idempotent(self):
        job = await self.enqueue()
        await self.enqueue()
        with patch('services.moderation.api', new_callable=AsyncMock, return_value=True) as api:
            await process_job(job.id)
            await process_job(job.id)
        api.assert_awaited_once_with(self.group, 'deleteMessage', chat_id='-1001', message_id=10)
        event = await EventLog.get()
        self.assertIn('关键词[广告]命中', event.result)
        self.assertIn('已删除消息', event.result)
        self.assertEqual(event.username, '@cat')
        self.assertEqual(event.content, '这是广告')

    async def test_whitelist_skips_ai_and_delete(self):
        await WhitelistSettings.create(id=1, entries=['@CAT'])
        await AISettings.create(id=1, enabled=True, base_url='https://example.invalid', model='test')
        job = await self.enqueue()
        with patch('services.antispam.classify_ai') as ai, patch('services.moderation.api', new_callable=AsyncMock) as api:
            await process_job(job.id)
        ai.assert_not_called()
        api.assert_not_awaited()
        self.assertEqual(await EventLog.all().count(), 0)

    async def test_ai_failure_never_deletes(self):
        from services.antispam import AIError
        await KeywordSettings.all().update(enabled=False)
        await AISettings.create(id=1, enabled=True, base_url='https://example.invalid', model='test')
        job = await self.enqueue()
        with patch('services.antispam.classify_ai', side_effect=AIError('AI 连接失败')), patch('services.moderation.api', new_callable=AsyncMock) as api:
            await process_job(job.id)
        api.assert_not_awaited()
        self.assertIn('判断未完成', (await EventLog.get()).result)

    async def test_edit_invalidates_old_spam_decision(self):
        old = await self.enqueue(1)
        self.message['text'] = '正常聊天'
        latest = await self.enqueue(2)
        with patch('services.moderation.api', new_callable=AsyncMock) as api:
            await process_job(old.id)
            await process_job(latest.id)
        api.assert_not_awaited()

    async def test_whitelist_changed_during_ai_prevents_delete(self):
        job = await self.enqueue()
        async def classify(*args):
            await WhitelistSettings.create(id=1, entries=['123'])
            return {'is_spam': True, 'message': '垃圾消息 AI 命中'}
        with patch('services.moderation.run_in_threadpool', side_effect=classify), patch('services.moderation.api', new_callable=AsyncMock) as api:
            await process_job(job.id)
        api.assert_not_awaited()

    async def test_rules_changed_during_judgment_prevents_delete(self):
        job = await self.enqueue()
        async def classify(*args):
            await KeywordSettings.all().update(keywords=['新规则'])
            return {'is_spam': True, 'message': '垃圾消息 旧关键词命中'}
        with patch('services.moderation.run_in_threadpool', side_effect=classify), patch('services.moderation.api', new_callable=AsyncMock) as api:
            await process_job(job.id)
        api.assert_not_awaited()

    async def test_delete_retry_and_final_failure_logged(self):
        job = await self.enqueue()
        with patch('services.moderation.api', new_callable=AsyncMock, side_effect=TelegramError('连接中断', uncertain=True)) as api:
            for _ in range(3):
                await ModerationJob.filter(id=job.id).update(available_at=0)
                await process_job(job.id)
        self.assertEqual(api.await_count, 3)
        self.assertIn('删除失败', (await EventLog.get()).result)
        self.assertEqual((await ModerationJob.get()).state, 'done')

    async def test_expired_job_not_deleted_and_caption_is_supported(self):
        self.message.pop('text')
        self.message['caption'] = '图片广告'
        job = await self.enqueue()
        await ModerationJob.all().update(expires_at=time.time() - 1)
        with patch('services.moderation.api', new_callable=AsyncMock) as api:
            await moderation_tick()
        api.assert_not_awaited()
        self.assertIn('超时', (await EventLog.get()).result)

    async def test_foreign_group_and_bot_messages_ignored(self):
        self.message['chat']['id'] = -1002
        await enqueue_message(self.group, self.message, 1)
        self.message['chat']['id'] = -1001
        self.message['from']['is_bot'] = True
        await enqueue_message(self.group, self.message, 2)
        self.assertEqual(await ModerationJob.all().count(), 0)

    async def test_all_messages_tracks_media_and_limits_user_scope(self):
        from dao.models import ModerationSettings
        await ModerationSettings.create(id=1, message_action='all')
        await enqueue_message(self.group, {**self.message, 'message_id': 8, 'text': '', 'photo': [{}]}, 2)
        await enqueue_message(self.group, {**self.message, 'message_id': 9, 'from': {'id': 999}, 'text': '正常'}, 3)
        job = await self.enqueue(4)
        with patch('services.moderation.api', new_callable=AsyncMock, return_value=True) as api:
            await process_job(job.id)
        import json
        self.assertEqual(api.await_args.args[1], 'deleteMessages')
        self.assertEqual(json.loads(api.await_args.kwargs['message_ids']), [8, 10])

    async def test_restriction_and_kick_precedence(self):
        from dao.models import ModerationSettings
        settings = await ModerationSettings.create(id=1, restrict_user=True)
        job = await self.enqueue()
        with patch('services.moderation.api', new_callable=AsyncMock, return_value=True) as api:
            await process_job(job.id)
        self.assertEqual([c.args[1] for c in api.await_args_list], ['deleteMessage', 'restrictChatMember'])
        self.assertEqual(api.await_args.kwargs['until_date'], 0)
        settings.kick_user = True
        await settings.save()
        self.message['message_id'] = 11
        job = await self.enqueue(2)
        with patch('services.moderation.api', new_callable=AsyncMock, return_value=True) as api:
            await process_job(job.id)
        self.assertEqual([c.args[1] for c in api.await_args_list], ['deleteMessage', 'banChatMember', 'unbanChatMember'])
        self.assertEqual(api.await_args.kwargs['only_if_banned'], 'true')

    async def test_member_retry_does_not_repeat_completed_delete(self):
        from dao.models import ModerationSettings
        await ModerationSettings.create(id=1, restrict_user=True)
        job = await self.enqueue()
        with patch('services.moderation.api', new_callable=AsyncMock, side_effect=[True, TelegramError('网络波动', retryable=True)]) as api:
            await process_job(job.id)
        await ModerationJob.filter(id=job.id).update(available_at=0)
        with patch('services.moderation.api', new_callable=AsyncMock, return_value=True) as api:
            await process_job(job.id)
        self.assertEqual([c.args[1] for c in api.await_args_list], ['restrictChatMember'])

    async def test_old_messages_are_not_punished_but_recent_edits_are_checked(self):
        self.message['date'] = int(time.time()) - 3600
        await enqueue_message(self.group, self.message, 1)
        self.assertEqual(await ModerationJob.all().count(), 0)
        self.message['edit_date'] = int(time.time())
        await enqueue_message(self.group, self.message, 2)
        self.assertEqual(await ModerationJob.all().count(), 1)
