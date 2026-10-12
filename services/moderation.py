"""广告拦截执行：先白名单，关键词与 AI 任一命中即删除。"""
import asyncio
import fcntl
import hashlib
import json
import time
from starlette.concurrency import run_in_threadpool
from tortoise.transactions import in_transaction
from dao.database import DATABASE_PATH
from dao.models import ObservedMessage, ModerationSettings, GroupConfig, KeywordSettings, AISettings, WhitelistSettings, ModerationJob, EventLog, event_time_now
from datetime import timedelta
from services.activity import record_activity
from services.antispam import classify_message
from services.join_runtime import bot_key, nickname, api
from services.permissions import RESTRICTED_PERMISSIONS
from services.network import get_proxy_url
from services.runtime_health import execution_allowed
from services.telegram import TelegramError


def rule_key(keywords, ai):
    values = [keywords.enabled, keywords.keywords] if keywords else [False, []]
    values += [ai.enabled, ai.base_url, ai.model, ai.api_key] if ai else [False, '', '', '']
    return hashlib.sha256(json.dumps(values, ensure_ascii=False).encode()).hexdigest()


async def enqueue_message(group, message, update_id):
    user = message.get('from') or {}
    text = message.get('text') or message.get('caption') or ''
    if str(message.get('chat', {}).get('id')) != group.chat_id or not user.get('id') or user.get('is_bot') or message.get('sender_chat'):
        return
    key = bot_key(group.bot_token)
    observed_id = hashlib.sha256(f'{key}:{group.chat_id}:{message["message_id"]}'.encode()).hexdigest()
    await ObservedMessage.get_or_create(id=observed_id, defaults={
        'bot_key': key, 'chat_id': group.chat_id, 'user_id': user['id'],
        'message_id': message['message_id'], 'sent_at': message.get('date') or time.time()})
    event_time = message.get('edit_date') or message.get('date')
    if not text or (event_time and time.time() - event_time > 300):
        return
    keywords = await KeywordSettings.get_or_none(id=1)
    ai = await AISettings.get_or_none(id=1)
    if not ((keywords and keywords.enabled) or (ai and ai.enabled)):
        return
    if await ModerationJob.filter(state__in=['queued', 'processing', 'deleting']).count() >= 2000:
        if not await EventLog.filter(event_type='机器人运行', content='广告拦截队列已满', created_at__gte=event_time_now() - timedelta(seconds=60)).exists():
            await EventLog.create(event_type='机器人运行', content='广告拦截队列已满', result='本次消息未处理 请检查 AI 响应速度与消息量')
        return
    key = bot_key(group.bot_token)
    job_id = hashlib.sha256(f'{key}:{group.chat_id}:{message["message_id"]}:{update_id}'.encode()).hexdigest()
    await ModerationJob.get_or_create(id=job_id, defaults={
        'bot_key': key, 'chat_id': group.chat_id, 'message_id': message['message_id'], 'update_id': update_id,
        'user': user, 'content': text[:10000], 'expires_at': time.time() + 300,
    })


async def finish(job, result, *, record=True):
    async with in_transaction():
        changed = await ModerationJob.filter(id=job.id).exclude(state='done').update(state='done', result=result)
        if changed and job.completed and any(job.operations[i]['method'] in ('deleteMessage', 'deleteMessages') for i in job.completed):
            await record_activity(job.bot_key, job.chat_id, 'blocked', job.message_id)
        if record and changed:
            await EventLog.create(event_type='广告拦截', nickname=nickname(job.user)[:128],
                username=('@' + job.user['username'])[:64] if job.user.get('username') else '', content=job.content, result=result[:500])


async def process_job(job_id):
    job = await ModerationJob.get_or_none(id=job_id)
    if not job or job.state not in ('queued', 'deleting') or job.available_at > time.time():
        return
    if job.expires_at <= time.time():
        progress = ' '.join(job.operations[i]['result'] for i in job.completed)
        await finish(job, (job.result + ' ' + progress + ' 处理超时 剩余操作未完成') if progress else '消息处理超时 未删除消息')
        return
    group = await GroupConfig.get_or_none(id=1)
    if not group or group.chat_id != job.chat_id or bot_key(group.bot_token) != job.bot_key:
        await finish(job, '群配置已变更 已跳过', record=False)
        return
    if not await execution_allowed(group):
        return
    # 编辑过的消息采用最新版本判断，旧判断不用于删除新内容。
    if await ModerationJob.filter(bot_key=job.bot_key, chat_id=job.chat_id, message_id=job.message_id, update_id__gt=job.update_id).exists():
        await finish(job, '消息已编辑 已跳过旧版本', record=False)
        return
    state = job.state
    claimed = await ModerationJob.filter(id=job.id, state=state).update(state='processing')
    if not claimed:
        return
    keywords = await KeywordSettings.get_or_none(id=1)
    ai = await AISettings.get_or_none(id=1)
    whitelist = await WhitelistSettings.get_or_none(id=1)
    if not ((keywords and keywords.enabled) or (ai and ai.enabled)):
        await finish(job, '广告拦截已关闭 已跳过', record=False)
        return
    if state == 'queued':
        job.rule_key = rule_key(keywords, ai)
        result = await run_in_threadpool(classify_message, job.content, keywords, ai, await get_proxy_url(),
            whitelist.entries if whitelist else [], str(job.user['id']), job.user.get('username', ''))
        if result['is_spam'] is not True:
            await finish(job, result['message'], record=result['is_spam'] is None)
            return
        job.result = result['message']
        ai_reason = next((check['detail'] for check in result.get('checks', []) if check.get('source') == 'AI' and check.get('status') == '垃圾'), '')
        if ai_reason:
            job.result += ' 原因 ' + ai_reason[:200]
        await job.save(update_fields=['result', 'rule_key'])
    # AI 返回期间可能发生关闭开关、白名单变更或消息编辑，删除前再次检查。
    latest_group = await GroupConfig.get_or_none(id=1)
    latest_keywords = await KeywordSettings.get_or_none(id=1)
    latest_ai = await AISettings.get_or_none(id=1)
    current_whitelist = await WhitelistSettings.get_or_none(id=1)
    identities = {str(job.user['id']), '@' + job.user.get('username', '').casefold()}
    whitelisted = identities.intersection(entry.casefold() for entry in (current_whitelist.entries if current_whitelist else []))
    changed = await ModerationJob.filter(bot_key=job.bot_key, chat_id=job.chat_id, message_id=job.message_id, update_id__gt=job.update_id).exists()
    if not latest_group or latest_group.revision != group.revision or not await execution_allowed(group) or whitelisted or changed or job.rule_key != rule_key(latest_keywords, latest_ai) or not ((latest_keywords and latest_keywords.enabled) or (latest_ai and latest_ai.enabled)):
        await finish(job, '配置或消息已变更 本次未删除消息')
        return
    if not job.operations:
        settings = await ModerationSettings.get_or_none(id=1)
        operations = []
        if settings and settings.message_action == 'all':
            ids = await ObservedMessage.filter(bot_key=job.bot_key, chat_id=job.chat_id,
                user_id=job.user['id'], sent_at__gt=time.time() - 48 * 3600 + 60).order_by('message_id').values_list('message_id', flat=True)
            ids = sorted(set(ids) | {job.message_id})
            for offset in range(0, len(ids), 100):
                operations.append({'method': 'deleteMessages', 'params': {'message_ids': json.dumps(ids[offset:offset + 100])}, 'result': '已清理该用户可删除的已接收消息'})
        else:
            operations.append({'method': 'deleteMessage', 'params': {'message_id': job.message_id}, 'result': '已删除消息'})
        if settings and settings.kick_user:
            # 有限封禁兜底，解除封禁阶段中断也不会永久阻止用户入群。
            operations += [
                {'method': 'banChatMember', 'params': {'user_id': job.user['id'], 'until_date': int(time.time()) + 60}, 'result': '已移出群组'},
                {'method': 'unbanChatMember', 'params': {'user_id': job.user['id'], 'only_if_banned': 'true'}, 'result': '已允许重新加入'},
            ]
        elif settings and settings.restrict_user:
            operations.append({'method': 'restrictChatMember', 'params': {
                'user_id': job.user['id'], 'permissions': json.dumps(RESTRICTED_PERMISSIONS),
                'use_independent_chat_permissions': 'true', 'until_date': 0}, 'result': '已封禁用户所有权限'})
        job.operations = operations
        await job.save(update_fields=['operations'])
    await ModerationJob.filter(id=job.id).update(state='deleting')
    for index, operation in enumerate(job.operations):
        if index in job.completed:
            continue
        if not await execution_allowed(group):
            return
        try:
            params = dict(operation['params'])
            if operation['method'] == 'banChatMember':
                params['until_date'] = int(time.time()) + 60
            await api(group, operation['method'], chat_id=job.chat_id, **params)
        except TelegramError as error:
            job.attempts += 1
            if job.attempts < 3 and (error.retryable or error.uncertain):
                await ModerationJob.filter(id=job.id).update(state='deleting', attempts=job.attempts, available_at=time.time() + max(2 ** job.attempts, error.retry_after))
            else:
                completed = ' '.join(job.operations[i]['result'] for i in job.completed)
                await finish(job, job.result + ' ' + completed + f' 删除失败或成员处置失败 共尝试 {job.attempts} 次 ' + str(error))
            return
        job.completed.append(index)
        job.attempts = 0
        await job.save(update_fields=['completed', 'attempts'])
    await finish(job, job.result + ' ' + ' '.join(operation['result'] for operation in job.operations))



async def moderation_tick():
    # 处理中断的判断可重新执行，删除本身可安全重复调用。
    now = time.time()
    await ModerationJob.filter(state='processing', expires_at__lt=now).update(state='queued')
    ids = await ModerationJob.filter(state__in=['queued', 'deleting'], available_at__lte=now).order_by('update_id').limit(3).values_list('id', flat=True)
    results = await asyncio.gather(*(process_job(job_id) for job_id in ids), return_exceptions=True)
    for job_id, result in zip(ids, results):
        if isinstance(result, Exception):
            job = await ModerationJob.get_or_none(id=job_id)
            if job:
                await finish(job, '消息处理异常 未完成删除请检查运行环境')


async def run_moderation_worker():
    path = DATABASE_PATH.parent / 'moderation-worker.lock'
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('a') as lock:
        while True:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                await asyncio.sleep(2)
        await ModerationJob.filter(state='processing').update(state='queued')
        last_cleanup = 0
        while True:
            await moderation_tick()
            if time.monotonic() - last_cleanup >= 60:
                await ModerationJob.filter(state='done', expires_at__lt=time.time() - 3600).delete()
                await ObservedMessage.filter(sent_at__lt=time.time() - 48 * 3600).delete()
                last_cleanup = time.monotonic()
            await asyncio.sleep(1)
