"""循环广播单次发送与结果日志。"""
from services.event_logs import record_event
import hashlib
import json
import asyncio
import time
import uuid

from starlette.concurrency import run_in_threadpool
from tortoise.transactions import in_transaction

from dao.models import BroadcastContent, BroadcastTask, EventLog, GroupConfig, BroadcastAttempt
from services.broadcast import preview_message
from services.runtime_health import execution_allowed
from services.network import get_proxy_url
from services.telegram import call_api, TelegramError


def build_keyboard(buttons):
    rows = {}
    for button in buttons:
        rows.setdefault(button['row'], []).append({'text': button['text'], 'url': button['url']})
    return {'inline_keyboard': [rows[number] for number in sorted(rows)]}


async def delete_previous_broadcast(task_id, current_attempt_id, group):
    """删除同一任务最近一次仍可追踪的成功广播，失败时留待下一轮重试。"""
    key = hashlib.sha256(group.bot_token.encode()).hexdigest()
    previous = await BroadcastAttempt.filter(
        task_id=task_id, bot_key=key, chat_id=group.chat_id, state='success', message_deleted=False,
    ).exclude(id=current_attempt_id).exclude(message_id=None).order_by('-started_at', '-id').first()
    if previous is None:
        return ''
    try:
        await run_in_threadpool(
            call_api, group.bot_token, 'deleteMessage', proxy_url=await get_proxy_url(),
            chat_id=group.chat_id, message_id=previous.message_id,
        )
    except TelegramError as error:
        return '删除上一条广播失败：' + str(error)
    await BroadcastAttempt.filter(
        id=previous.id, message_id=previous.message_id, message_deleted=False,
    ).update(message_deleted=True)
    return '已删除上一条广播'


async def deliver_broadcast(task_id, attempt_id=None):
    """每次真实发送尝试写一条结果；关闭或已删除的任务直接跳过。"""
    task = await BroadcastTask.get_or_none(id=task_id)
    if task is None or not task.enabled:
        if attempt_id:
            async with in_transaction():
                changed = await BroadcastAttempt.filter(id=attempt_id, state='queued').update(state='skipped')
                if changed:
                    await record_event(flow_id=f'broadcast:{attempt_id}', event_type='群广播', content=f'任务 {task_id}', result='任务已关闭或删除 本次计划已跳过')
        return None
    content = await BroadcastContent.get_or_none(id=task.content_id)
    if content is None:
        return None
    group = await GroupConfig.get_or_none(id=1)
    text = preview_message(content.message)
    attempt_id = attempt_id or uuid.uuid4().hex
    attempt = await BroadcastAttempt.get_or_none(id=attempt_id)
    if attempt and attempt.state != 'queued':
        return None
    if attempt is None:
        attempt = await BroadcastAttempt.create(id=attempt_id, task_id=task_id, state='queued', started_at=time.time(), content=text)
    bot_key = hashlib.sha256(group.bot_token.encode()).hexdigest() if group and group.bot_token else ''
    chat_id = group.chat_id if group else ''
    claimed = await BroadcastAttempt.filter(id=attempt_id, state='queued').update(
        state='sending', started_at=time.time(), content=text,
        bot_key=bot_key, chat_id=chat_id,
    )
    if not claimed:
        return None
    check = group.check_result if group and isinstance(group.check_result, dict) else {}
    nickname = check.get('bot_name', '')
    user_id = check.get('bot_id')
    message_id = None
    attempts = 0
    deletion_result = ''
    try:
        if group is None or not group.bot_token or not group.chat_id:
            raise TelegramError('尚未配置机器人与目标群。')
        if not text.strip() or len(text) > 4096:
            raise TelegramError('广播内容为空或超过 4096 个字符。')
        params = {'chat_id': group.chat_id, 'text': text}
        if content.buttons:
            params['reply_markup'] = json.dumps(build_keyboard(content.buttons), ensure_ascii=False)
        for number in range(3):
            if not await BroadcastTask.filter(id=task_id, enabled=True).exists() or not await execution_allowed(group):
                raise TelegramError('任务已停止 本次发送已取消')
            current = await GroupConfig.get_or_none(id=1)
            if current is None or current.revision != group.revision:
                raise TelegramError('群配置已变更 本次发送已取消')
            if task.delete_previous and not deletion_result:
                deletion_result = await delete_previous_broadcast(task_id, attempt_id, group)
            try:
                attempts += 1
                response = await run_in_threadpool(
                    call_api, group.bot_token, 'sendMessage', proxy_url=await get_proxy_url(), **params,
                )
                break
            except TelegramError as error:
                if error.uncertain or not error.retryable or number == 2 or error.retry_after > 10:
                    raise
                await asyncio.sleep(error.retry_after or (number + 1) * 2)
        message_id = response.get('message_id')
        if type(message_id) is not int:
            raise TelegramError('未获得发送确认', uncertain=True)
        sender = response.get('from') or {}
        nickname = sender.get('first_name') or nickname
        user_id = sender.get('id') or user_id
        delivery_result = '发送成功' if attempts == 1 else f'发送成功 共尝试 {attempts} 次'
        result = ' '.join(value for value in (deletion_result, delivery_result) if value)
        attempt_state = 'success'
        ok = True
    except TelegramError as error:
        delivery_result = ('发送结果不确定 为避免重复未重发 ' if error.uncertain else '发送失败：') + str(error)
        if attempts > 1:
            delivery_result += f' 共尝试 {attempts} 次'
        result = ' '.join(value for value in (deletion_result, delivery_result) if value)
        attempt_state = 'unknown' if error.uncertain else 'failed'
        ok = False
    # 日志与最终状态一起提交，避免状态完成却没有结果日志。
    async with in_transaction():
        await BroadcastAttempt.filter(id=attempt_id).update(
            state=attempt_state, message_id=message_id if ok else None, message_deleted=False,
        )
        await record_event(
            flow_id=f'broadcast:{attempt_id}', event_type='群广播', nickname=nickname[:128],
            user_id=user_id,
            content=text, result=result[:500],
        )
    return {'ok': ok, 'message_id': message_id, 'message': result}
