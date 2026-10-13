"""真实入群流程的模拟接口测试，不向 Telegram 发消息。"""
import time
import unittest
from unittest.mock import AsyncMock, patch
from tortoise import Tortoise
from dao.models import GroupConfig, VerificationSettings, WelcomeSettings, JoinSession, EventLog
from services.join_runtime import process_join, process_callback, maintain
from services.telegram_worker import poll_once


class JoinTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        await Tortoise.init(db_url='sqlite://:memory:', modules={'models': ['dao.models']})
        await Tortoise.generate_schemas()
        self.group = await GroupConfig.create(id=1, bot_token='test', chat_id='-1001', revision='v1')
        self.settings = await VerificationSettings.create(id=1, enabled=True)
        await WelcomeSettings.create(id=1, enabled=True, message='欢迎 {nickname} {username}', delete_after_seconds=1)
        self.calls = []
        async def api(group, method, **params):
            self.calls.append((method, params))
            if method == 'getChat': return {'type': 'supergroup', 'permissions': {'can_send_messages': True, 'can_send_photos': False}}
            if method == 'getChatMember': return {'status': 'member'}
            if method == 'sendMessage': return {'message_id': len(self.calls)}
            return True
        self.mock = patch('services.join_runtime.api', side_effect=api)
        self.mock.start()
        self.event = {'chat': {'id': -1001}, 'old_chat_member': {'status': 'left'},
                      'new_chat_member': {'status': 'member', 'user': {'id': 123, 'first_name': '小猫', 'username': 'cat'}}}

    async def asyncTearDown(self):
        self.mock.stop()
        await Tortoise.close_connections()

    def callback(self, session, user=123, answer=None):
        return {'id': 'query', 'from': {'id': user}, 'data': f'join:{session.id}:{answer or session.answer}',
                'message': {'message_id': session.message_id, 'chat': {'id': -1001}}}

    async def test_button_pass_delete_then_welcome_and_duplicate(self):
        await process_join(self.group, self.event, 1)
        await process_join(self.group, self.event, 1)
        session = await JoinSession.get()
        self.assertEqual(session.state, 'pending')
        self.assertEqual(len([x for x in self.calls if x[0] == 'sendMessage']), 1)
        await process_callback(self.group, self.callback(session, user=456))
        self.assertEqual((await JoinSession.get()).state, 'pending')
        await process_callback(self.group, self.callback(session))
        session = await JoinSession.get()
        self.assertEqual(session.state, 'welcome')
        methods = [x[0] for x in self.calls]
        self.assertLess(methods.index('deleteMessage'), len(methods) - 1)
        self.assertEqual(self.calls[-1][1]['text'], '欢迎 小猫 @cat')
        await JoinSession.filter(id=session.id).update(delete_at=time.time() - 1)
        await maintain(self.group)
        self.assertEqual((await JoinSession.get()).state, 'done')
        self.assertTrue(await EventLog.filter(result='验证通过').exists())
        self.assertTrue(await EventLog.filter(event_type='入群欢迎').exists())

    async def test_arithmetic_requires_correct_answer(self):
        await VerificationSettings.filter(id=1).update(method='arithmetic')
        await process_join(self.group, self.event, 2)
        session = await JoinSession.get()
        self.assertEqual(len(set(session.choices)), 4)
        self.assertIn(session.answer, session.choices)
        wrong = next(value for value in session.choices if value != session.answer)
        await process_callback(self.group, self.callback(session, answer=wrong))
        self.assertEqual((await JoinSession.get()).state, 'pending')
        await process_callback(self.group, self.callback(session))
        self.assertEqual((await JoinSession.get()).state, 'welcome')

    async def test_timeout_removes_and_no_welcome(self):
        await process_join(self.group, self.event, 3)
        await JoinSession.all().update(deadline=time.time() - 1, retry_at=0)
        await maintain(self.group)
        self.assertEqual((await JoinSession.get()).state, 'done')
        self.assertIn('banChatMember', [x[0] for x in self.calls])
        self.assertFalse(await EventLog.filter(event_type='入群欢迎').exists())

    async def test_welcome_without_verification_and_no_username(self):
        await VerificationSettings.all().update(enabled=False)
        self.event['new_chat_member']['user'].pop('username')
        await process_join(self.group, self.event, 4)
        self.assertEqual(self.calls[-1][1]['text'], '欢迎 小猫 小猫')
        self.assertNotIn('restrictChatMember', [x[0] for x in self.calls])

    async def test_disabling_releases_pending_without_welcome(self):
        await process_join(self.group, self.event, 5)
        await VerificationSettings.all().update(enabled=False)
        await maintain(self.group)
        self.assertEqual((await JoinSession.get()).state, 'done')
        self.assertFalse(await EventLog.filter(event_type='入群欢迎').exists())
        self.assertEqual(self.calls[-2][1]['permissions'], '{"can_send_messages": true, "can_send_photos": false}')

    async def test_other_group_existing_member_and_bots_ignored(self):
        self.event['chat']['id'] = -1002
        await process_join(self.group, self.event, 6)
        self.event['chat']['id'] = -1001
        self.event['old_chat_member']['status'] = 'member'
        await process_join(self.group, self.event, 7)
        self.event['old_chat_member']['status'] = 'left'
        self.event['new_chat_member']['user']['is_bot'] = True
        await process_join(self.group, self.event, 8)
        self.assertEqual(await JoinSession.all().count(), 0)

    async def test_poll_persists_offset(self):
        from dao.models import TelegramCursor
        updates = [{'update_id': 42, 'chat_member': self.event}]
        with patch('services.telegram_worker.api', new_callable=AsyncMock, return_value=updates) as api:
            await poll_once(self.group)
            self.assertEqual((await TelegramCursor.get()).offset, 43)
            self.assertEqual(api.call_args.kwargs['offset'], 0)
            await poll_once(self.group)
            self.assertEqual(api.call_args.kwargs['offset'], 43)
            self.assertEqual(await JoinSession.all().count(), 1)

    async def test_stale_join_and_admin_ignored(self):
        self.event['date'] = int(time.time()) - 600
        await process_join(self.group, self.event, 10)
        self.assertEqual(await JoinSession.all().count(), 0)
        self.event.pop('date')
        with patch('services.join_runtime.api', new_callable=AsyncMock, return_value={'status': 'administrator'}):
            await process_join(self.group, self.event, 11)
        self.assertEqual(await JoinSession.all().count(), 0)

    async def test_send_failure_releases_without_kicking(self):
        from services.telegram import TelegramError
        original = __import__('services.join_runtime', fromlist=['api']).api
        async def fail_send(group, method, **params):
            if method == 'sendMessage': raise TelegramError('模拟发送失败')
            return await original(group, method, **params)
        with patch('services.join_runtime.api', side_effect=fail_send):
            await process_join(self.group, self.event, 12)
        self.assertEqual((await JoinSession.get()).state, 'restricted')
        await JoinSession.all().update(deadline=time.time() - 1, retry_at=0)
        await maintain(self.group)
        self.assertEqual((await JoinSession.get()).state, 'done')
        self.assertNotIn('banChatMember', [x[0] for x in self.calls])

    async def test_delete_failure_blocks_welcome_and_recovers(self):
        from services.telegram import TelegramError
        await process_join(self.group, self.event, 13)
        session = await JoinSession.get()
        original = __import__('services.join_runtime', fromlist=['api']).api
        async def fail_delete(group, method, **params):
            if method == 'deleteMessage': raise TelegramError('模拟删除失败')
            return await original(group, method, **params)
        with patch('services.join_runtime.api', side_effect=fail_delete):
            await process_callback(self.group, self.callback(session))
        self.assertEqual((await JoinSession.get()).state, 'passing')
        self.assertFalse(await EventLog.filter(event_type='入群欢迎').exists())
        await JoinSession.all().update(retry_at=0)
        await maintain(self.group)
        self.assertEqual((await JoinSession.get()).state, 'welcome')

    async def test_uncertain_prompt_not_resent_but_owner_can_finish(self):
        from services.telegram import TelegramError
        original = __import__('services.join_runtime', fromlist=['api']).api
        async def timeout(group, method, **params):
            if method == 'sendMessage': raise TelegramError('发送超时', uncertain=True)
            return await original(group, method, **params)
        with patch('services.join_runtime.api', side_effect=timeout):
            await process_join(self.group, self.event, 14)
        session = await JoinSession.get()
        self.assertEqual(session.state, 'uncertain')
        await maintain(self.group)
        self.assertEqual((await JoinSession.get()).state, 'uncertain')
        callback = self.callback(session)
        callback['message']['message_id'] = 100
        await process_callback(self.group, callback)
        self.assertEqual((await JoinSession.get()).state, 'welcome')

    async def test_interrupted_welcome_never_sent_again(self):
        await VerificationSettings.all().update(enabled=False)
        await process_join(self.group, self.event, 15)
        await JoinSession.all().update(state='sending_welcome')
        count = len([item for item in self.calls if item[0] == 'sendMessage'])
        await maintain(self.group)
        self.assertEqual(len([item for item in self.calls if item[0] == 'sendMessage']), count)
        self.assertEqual((await JoinSession.get()).state, 'done')

    async def test_disabled_pending_timeout_does_not_kick(self):
        await process_join(self.group, self.event, 16)
        await JoinSession.all().update(state='timed_out')
        await VerificationSettings.all().update(enabled=False)
        await maintain(self.group)
        self.assertNotIn('banChatMember', [item[0] for item in self.calls])


    async def test_timeout_restricts_permanently_without_welcome(self):
        await VerificationSettings.all().update(timeout_action='restrict')
        await process_join(self.group, self.event, 301)
        await JoinSession.all().update(deadline=time.time() - 1, retry_at=0)
        self.calls.clear()
        await maintain(self.group)
        restriction = next(params for method, params in self.calls if method == 'restrictChatMember')
        self.assertEqual(restriction['until_date'], 0)
        self.assertNotIn('banChatMember', [method for method, params in self.calls])
        self.assertNotIn('sendMessage', [method for method, params in self.calls])
        self.assertTrue(await EventLog.filter(result='验证超时 已限制用户所有权限').exists())

    async def test_wrong_answer_actions_and_old_button_invalid(self):
        for number, action in enumerate(['restrict', 'kick'], 310):
            await VerificationSettings.all().update(method='arithmetic', wrong_action=action)
            await process_join(self.group, self.event, number)
            session = await JoinSession.get(state='pending')
            wrong = next(value for value in session.choices if value != session.answer)
            self.calls.clear()
            await process_callback(self.group, self.callback(session, answer=wrong))
            self.assertEqual((await JoinSession.get(id=session.id)).state, 'done')
            methods = [method for method, params in self.calls]
            self.assertIn('restrictChatMember' if action == 'restrict' else 'banChatMember', methods)
            self.assertLess(methods.index('deleteMessage'), methods.index('getChatMember'))
            self.assertLess(methods.index('deleteMessage'), methods.index('restrictChatMember' if action == 'restrict' else 'banChatMember'))
            if action == 'kick':
                self.assertIn('unbanChatMember', methods)
            self.assertNotIn('sendMessage', methods)
            self.calls.clear()
            await process_callback(self.group, self.callback(session))
            self.assertEqual([method for method, params in self.calls], ['answerCallbackQuery'])

    async def test_no_action_keeps_restriction_deadline_and_records_wrong_answer(self):
        await VerificationSettings.all().update(method='arithmetic', wrong_action='none')
        await process_join(self.group, self.event, 320)
        session = await JoinSession.get()
        wrong = next(value for value in session.choices if value != session.answer)
        self.calls.clear()
        await process_callback(self.group, self.callback(session, answer=wrong))
        self.assertEqual([method for method, params in self.calls], ['answerCallbackQuery'])
        updated = await JoinSession.get()
        self.assertEqual(updated.state, 'pending')
        self.assertEqual(updated.deadline, session.deadline)
        self.assertTrue(await EventLog.filter(result='回答错误 未执行额外处理 保持待验证状态').exists())

    async def test_kick_unban_failure_resumes_without_repeating_ban(self):
        from services.telegram import TelegramError
        await process_join(self.group, self.event, 330)
        await JoinSession.all().update(deadline=time.time() - 1, retry_at=0)
        async def failing(group, method, **params):
            if method == 'getChatMember': return {'status': 'member'}
            if method == 'unbanChatMember': raise TelegramError('网络波动', retryable=True)
            return True
        with patch('services.join_runtime.api', side_effect=failing):
            await maintain(self.group)
        self.assertEqual((await JoinSession.get()).state, 'failed_kick')
        await JoinSession.all().update(retry_at=0)
        self.calls.clear()
        await maintain(self.group)
        self.assertNotIn('banChatMember', [method for method, params in self.calls])
        self.assertEqual((await JoinSession.get()).state, 'done')
        self.assertTrue(await EventLog.filter(result='验证超时 已移出群组并允许重新加入').exists())

    async def test_rate_limited_actions_wait_before_retry(self):
        from services.telegram import TelegramError
        await process_join(self.group, self.event, 340)
        session = await JoinSession.get()
        async def limited(group, method, **params):
            if method == 'deleteMessage': raise TelegramError('限流', retryable=True, retry_after=30)
            return True
        with patch('services.join_runtime.api', side_effect=limited):
            await process_callback(self.group, self.callback(session))
        session = await JoinSession.get()
        self.assertGreater(session.retry_at, time.time() + 25)
        self.calls.clear()
        await maintain(self.group)
        self.assertEqual(self.calls, [])

    async def test_old_session_cannot_act_after_binding_changes_even_if_state_was_restored(self):
        await process_join(self.group, self.event, 350)
        session = await JoinSession.get()
        self.group.revision = 'new-binding'
        await self.group.save()
        self.calls.clear()
        await process_callback(self.group, self.callback(session))
        self.assertEqual([method for method, params in self.calls], ['answerCallbackQuery'])
        self.calls.clear()
        await maintain(self.group)
        self.assertEqual(self.calls, [])
        self.assertEqual((await JoinSession.get()).state, 'done')

    async def test_new_member_restricted_before_prompt_and_released_only_after_pass(self):
        import json
        await process_join(self.group, self.event, 360)
        methods = [method for method, params in self.calls]
        self.assertLess(methods.index('restrictChatMember'), methods.index('sendMessage'))
        restriction = next(params for method, params in self.calls if method == 'restrictChatMember')
        permissions = json.loads(restriction['permissions'])
        for field in ['can_send_messages', 'can_send_photos', 'can_send_videos', 'can_send_documents', 'can_send_other_messages']:
            self.assertFalse(permissions[field])
        self.assertEqual(restriction['use_independent_chat_permissions'], 'true')
        session = await JoinSession.get()
        self.calls.clear()
        await process_callback(self.group, self.callback(session))
        methods = [method for method, params in self.calls]
        self.assertLess(methods.index('deleteMessage'), methods.index('restrictChatMember'))
        restored = next(params for method, params in self.calls if method == 'restrictChatMember')
        self.assertEqual(json.loads(restored['permissions']), {'can_send_messages': True, 'can_send_photos': False})


    async def test_timeout_deletes_prompt_before_member_action(self):
        for index, action in enumerate(('kick', 'restrict'), 1000):
            await VerificationSettings.all().update(timeout_action=action)
            await process_join(self.group, self.event, index)
            session = await JoinSession.get(state='pending')
            await JoinSession.filter(id=session.id).update(deadline=time.time() - 1)
            self.calls.clear()
            await maintain(self.group)
            methods = [method for method, params in self.calls]
            self.assertLess(methods.index('deleteMessage'), methods.index('getChatMember'))
            self.assertLess(methods.index('deleteMessage'), methods.index('banChatMember' if action == 'kick' else 'restrictChatMember'))

    async def test_permission_retry_does_not_repeat_deleted_prompt(self):
        from services.telegram import TelegramError
        await process_join(self.group, self.event, 1100)
        session = await JoinSession.get()
        original = __import__('services.join_runtime', fromlist=['api']).api
        async def fail_restore(group, method, **params):
            if method == 'restrictChatMember':
                raise TelegramError('网络波动', retryable=True)
            return await original(group, method, **params)
        self.calls.clear()
        with patch('services.join_runtime.api', side_effect=fail_restore):
            await process_callback(self.group, self.callback(session))
        saved = await JoinSession.get()
        self.assertIsNone(saved.message_id)
        self.assertEqual(saved.state, 'passing')
        await JoinSession.all().update(retry_at=0)
        self.calls.clear()
        await maintain(self.group)
        methods = [method for method, params in self.calls]
        self.assertNotIn('deleteMessage', methods)
        self.assertIn('restrictChatMember', methods)
        self.assertEqual((await JoinSession.get()).state, 'welcome')
