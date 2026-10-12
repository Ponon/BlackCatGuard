"""单群入群流程，所有 Telegram 操作均通过统一代理入口。"""
import hashlib
import json
import secrets
import time

from starlette.concurrency import run_in_threadpool
from dao.models import GroupConfig, VerificationSettings, WelcomeSettings, JoinSession, EventLog
from services.activity import record_activity
from services.configuration import verification_is_enabled, welcome_is_enabled
from services.permissions import RESTRICTED_PERMISSIONS
from services.network import get_proxy_url
from services.telegram import call_api, TelegramError
from services.runtime_health import execution_allowed


def bot_key(token):
    return hashlib.sha256(token.encode()).hexdigest()


async def api(group, method, **params):
    if not await execution_allowed(group):
        raise TelegramError('连接状态异常 已暂停执行')
    return await run_in_threadpool(call_api, group.bot_token, method, proxy_url=await get_proxy_url(), **params)


def nickname(user):
    return ' '.join(filter(None, [user.get('first_name'), user.get('last_name')])) or str(user['id'])


async def log(session, result, event_type='入群验证', content=None):
    await EventLog.create(event_type=event_type, nickname=nickname(session.user)[:128],
                          username=('@' + session.user['username'])[:64] if session.user.get('username') else '',
                          content=content if content is not None else session.question, result=result[:500])


async def change_state(session, state, **values):
    session.state = state
    session.retry_at = 0
    session.failure_count = 0
    for key, value in values.items():
        setattr(session, key, value)
    await session.save(update_fields=['state', 'retry_at', 'failure_count', *values])


async def delete_message(group, session):
    if session.message_id:
        await api(group, 'deleteMessage', chat_id=session.chat_id, message_id=session.message_id)
        session.message_id = None
        await session.save(update_fields=['message_id'])


async def welcome(group, session):
    settings = await WelcomeSettings.get_or_none(id=1)
    if not welcome_is_enabled(settings, True):
        await change_state(session, 'done')
        return
    user = session.user
    text = settings.message.replace('{nickname}', nickname(user)).replace(
        '{username}', '@' + user['username'] if user.get('username') else nickname(user),
    )
    await change_state(session, 'sending_welcome')
    result = await api(group, 'sendMessage', chat_id=session.chat_id, text=text)
    await change_state(session, 'welcome', message_id=result['message_id'],
                       delete_at=time.time() + settings.delete_after_seconds if settings.delete_after_seconds else 0)
    await log(session, '欢迎语发送成功', '入群欢迎', text)
    if not settings.delete_after_seconds:
        await change_state(session, 'done')


async def advance(group, session):
    """每个外部操作后保存进度，失败留待下一轮恢复。"""
    if session.config_revision and session.config_revision != group.revision:
        await change_state(session, 'done')
        return
    if session.retry_at > time.time():
        return
    if session.state == 'new':
        info = await api(group, 'getChat', chat_id=session.chat_id)
        if info.get('type') != 'supergroup':
            raise TelegramError('入群验证需要超级群组')
        session.permissions = info.get('permissions') or {'can_send_messages': True}
        await session.save(update_fields=['permissions'])
        await api(group, 'restrictChatMember', chat_id=session.chat_id, user_id=session.user_id,
                  permissions=json.dumps(RESTRICTED_PERMISSIONS), use_independent_chat_permissions='true',
                  until_date=max(int(session.deadline) + 60, int(time.time()) + 60))
        await change_state(session, 'restricted')
    if session.state == 'restricted':
        keyboard = [[{'text': '点击验证' if value == 'ok' else value, 'callback_data': f'join:{session.id}:{value}'} for value in session.choices]]
        await change_state(session, 'sending_prompt')
        result = await api(group, 'sendMessage', chat_id=session.chat_id, text=session.question,
                           reply_markup=json.dumps({'inline_keyboard': keyboard}, ensure_ascii=False))
        await change_state(session, 'pending', message_id=result['message_id'])
        await log(session, '等待验证')
    if session.state == 'passing':
        # 恢复入群时群组默认权限，不额外授予管理员权限。
        await api(group, 'restrictChatMember', chat_id=session.chat_id, user_id=session.user_id,
                  permissions=json.dumps(session.permissions), use_independent_chat_permissions='true')
        await change_state(session, 'passed')
    if session.state in ('passed', 'cancelled'):
        await delete_message(group, session)
        state = session.state
        await change_state(session, 'greeting' if state == 'passed' else 'done')
        await log(session, '验证通过' if state == 'passed' else '验证已取消并解除限制')
    if session.state == 'abandoned':
        await delete_message(group, session)
        await change_state(session, 'done')
    if session.state == 'timed_out':
        settings = await VerificationSettings.get_or_none(id=1)
        await change_state(session, 'failed_action', failure_action=settings.timeout_action if settings else 'kick', failure_reason='验证超时')
    if session.state == 'failed_action':
        member = await api(group, 'getChatMember', chat_id=session.chat_id, user_id=session.user_id)
        if member.get('status') in ('administrator', 'creator', 'left', 'kicked'):
            await change_state(session, 'failed_clean', failure_action='skip')
            session.failure = '成员已离群或身份变更已停止处置'
            await session.save(update_fields=['failure'])
        elif session.failure_action == 'restrict':
            await api(group, 'restrictChatMember', chat_id=session.chat_id, user_id=session.user_id,
                      permissions=json.dumps(RESTRICTED_PERMISSIONS), use_independent_chat_permissions='true', until_date=0)
            await change_state(session, 'failed_clean', failure='已限制用户所有权限')
        else:
            # 短期封禁兜底，解除阶段失败也不会留下永久封禁。
            await api(group, 'banChatMember', chat_id=session.chat_id, user_id=session.user_id, until_date=int(time.time()) + 60)
            await change_state(session, 'failed_kick')
    if session.state == 'failed_kick':
        await api(group, 'unbanChatMember', chat_id=session.chat_id, user_id=session.user_id, only_if_banned='true')
        await change_state(session, 'failed_clean', failure='已移出群组并允许重新加入')
    if session.state in ('failed_clean', 'removed'):
        await delete_message(group, session)
        await change_state(session, 'done')
        outcome = {'restrict': '已限制用户所有权限', 'kick': '已移出群组并允许重新加入', 'skip': '成员已离群或身份变更已停止处置'}.get(session.failure_action, '已移出群组')
        await log(session, (session.failure_reason or '验证超时') + ' ' + outcome)
    if session.state == 'greeting':
        await welcome(group, session)
    if session.state == 'welcome' and session.delete_at and session.delete_at <= time.time():
        await delete_message(group, session)
        await change_state(session, 'done')


async def record_failure(session, error):
    # 发消息超时可能已经投递成功，不重复发送验证或欢迎消息。
    if error.uncertain:
        if session.state == 'sending_prompt':
            await change_state(session, 'uncertain')
        elif session.state == 'sending_welcome':
            await change_state(session, 'done')
    elif session.state == 'sending_prompt':
        await change_state(session, 'restricted')
    elif session.state == 'sending_welcome':
        await change_state(session, 'greeting')
    session.failure_count = min(session.failure_count + 1, 6)
    session.retry_at = time.time() + max(2 ** session.failure_count, error.retry_after)
    await session.save(update_fields=['failure_count', 'retry_at'])
    message = str(error)
    if session.failure != message:
        await log(session, '处理失败 ' + message)
        session.failure = message
        await session.save(update_fields=['failure'])


async def process_join(group, event, update_id):
    old = event.get('old_chat_member', {})
    new = event.get('new_chat_member', {})
    user = new.get('user') or {}
    was_member = old.get('status') in ('member', 'administrator', 'creator') or (old.get('status') == 'restricted' and old.get('is_member'))
    if was_member or new.get('status') != 'member' or user.get('is_bot') or not user.get('id'):
        return
    if str(event.get('chat', {}).get('id')) != group.chat_id:
        return
    event_time = event.get('date', time.time())
    if time.time() - event_time <= 300:
        await record_activity(bot_key(group.bot_token), group.chat_id, 'join', update_id, event_time)
    settings = await VerificationSettings.get_or_none(id=1)
    enabled = verification_is_enabled(settings, True)
    welcome_settings = await WelcomeSettings.get_or_none(id=1)
    if not enabled and not welcome_is_enabled(welcome_settings, True):
        return
    # 初次接入或长时间停机后的旧入群事件不重新验证历史成员。
    event_age = time.time() - event.get('date', time.time())
    if event_age > (settings.timeout_seconds if enabled else 300):
        return
    current = await api(group, 'getChatMember', chat_id=group.chat_id, user_id=user['id'])
    if current.get('status') != 'member':
        return
    # 同一个更新重放时沿用已有会话，避免重复禁言和欢迎。
    key = bot_key(group.bot_token)
    session_id = hashlib.sha256(f'{key}:{update_id}:{user["id"]}'.encode()).hexdigest()[:32]
    if await JoinSession.filter(id=session_id).exists():
        return
    # 成员重新加入时废弃旧挑战，旧按钮不能操作新的入群会话。
    await JoinSession.filter(bot_key=key, chat_id=group.chat_id, user_id=user['id']).exclude(state__in=['done', 'welcome']).update(state='abandoned')
    answer, choices = 'ok', ['ok']
    question = f'{nickname(user)} 请点击下方按钮完成入群验证'
    if enabled and settings.method == 'arithmetic':
        left, right = secrets.randbelow(9) + 1, secrets.randbelow(9) + 1
        answer = str(left + right)
        options = {answer}
        while len(options) < 4:
            options.add(str(secrets.randbelow(25)))
        choices = list(options)
        secrets.SystemRandom().shuffle(choices)
        question = f'{nickname(user)} 请选出正确答案 {left} + {right} = ?'
    elif enabled:
        choices = ['ok']
    session = await JoinSession.create(id=session_id, bot_key=key, chat_id=group.chat_id, user_id=user['id'], user=user,
        question=question, answer=answer, choices=choices, deadline=time.time() + settings.timeout_seconds if enabled else 0,
        state='new' if enabled else 'greeting', config_revision=group.revision)
    try:
        await advance(group, session)
    except TelegramError as error:
        await record_failure(session, error)


async def process_callback(group, callback):
    data = callback.get('data', '').split(':')
    if len(data) != 3 or data[0] != 'join':
        return
    session = await JoinSession.get_or_none(id=data[1], bot_key=bot_key(group.bot_token), chat_id=group.chat_id)
    message = callback.get('message') or {}
    text = '验证已失效'
    if session and callback.get('from', {}).get('id') != session.user_id:
        text = '请由入群成员本人完成验证'
    elif session and session.config_revision in ('', group.revision) and session.state in ('pending', 'uncertain') and session.deadline > time.time() and str(message.get('chat', {}).get('id')) == session.chat_id and (session.message_id is None or message.get('message_id') == session.message_id):
        if session.message_id is None:
            session.message_id = message['message_id']
            await session.save(update_fields=['message_id'])
        settings = await VerificationSettings.get_or_none(id=1)
        if not verification_is_enabled(settings, True):
            text = '验证已关闭'
        elif data[2] == session.answer:
            await change_state(session, 'passing')
            text = '验证通过'
        elif data[2] in session.choices:
            if settings.wrong_action == 'none':
                text = '答案不正确 尚未通过验证'
                await log(session, '回答错误 未执行额外处理 保持待验证状态')
            else:
                await change_state(session, 'failed_action', failure_action=settings.wrong_action, failure_reason='回答错误')
                text = '答案不正确 验证未通过'
    await api(group, 'answerCallbackQuery', callback_query_id=callback['id'], text=text)
    if session and session.state in ('passing', 'failed_action'):
        try:
            await advance(group, session)
        except TelegramError as error:
            await record_failure(session, error)


async def maintain(group):
    settings = await VerificationSettings.get_or_none(id=1)
    enabled = verification_is_enabled(settings, True)
    sessions = await JoinSession.filter(bot_key=bot_key(group.bot_token), chat_id=group.chat_id).exclude(state='done')
    for session in sessions:
        if session.config_revision and session.config_revision != group.revision:
            await change_state(session, 'done')
            continue
        if session.retry_at > time.time():
            continue
        try:
            if session.state in ('sending_prompt', 'sending_welcome'):
                await log(session, '上次发送中断 结果不确定 未自动重发')
                await change_state(session, 'uncertain' if session.state == 'sending_prompt' else 'done')
            if session.state == 'new' and not enabled:
                await change_state(session, 'done')
                continue
            if session.state in ('new', 'restricted', 'pending', 'uncertain', 'timed_out'):
                if not enabled or (session.state in ('restricted', 'uncertain') and session.deadline <= time.time()):
                    await api(group, 'restrictChatMember', chat_id=session.chat_id, user_id=session.user_id,
                              permissions=json.dumps(session.permissions or {'can_send_messages': True}), use_independent_chat_permissions='true')
                    await change_state(session, 'cancelled')
                elif session.deadline <= time.time():
                    if session.state == 'new':
                        await change_state(session, 'done')
                        await log(session, '无法启动验证请检查机器人权限')
                        continue
                    await change_state(session, 'timed_out')
            await advance(group, session)
        except TelegramError as error:
            await record_failure(session, error)
