"""系统事件通知独立排队，推送故障不反向触发推送。"""
import asyncio
import fcntl
import json
import re
import socket
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request

from starlette.concurrency import run_in_threadpool
from tortoise.signals import post_save
from tortoise.transactions import in_transaction
from dao.database import DATABASE_PATH
from dao.models import PushSettings, PushJob, EventLog
from services.network import open_request
from services.event_logs import record_event

# 所有事件通知与测试推送共用此固定分组
BARK_GROUP = "黑猫群管"
BARK_TITLE = "黑猫群管"

EVENT_OPTIONS = {
    'ad_block': '广告拦截', 'verification_passed': '验证通过', 'verification_timeout': '验证超时', 'verification_wrong': '验证答错',
    'broadcast_success': '广播成功', 'broadcast_failed': '广播失败', 'connection_failed': '连接异常', 'connection_restored': '连接恢复',
    'runtime_failed': '运行异常',
}


class PushError(Exception):
    def __init__(self, message, *, retryable=False, uncertain=False):
        super().__init__(message)
        self.retryable, self.uncertain = retryable, uncertain


def event_key(event):
    if event.event_type == '广告拦截':
        if event.result.startswith('综合判定：正常消息'):
            return None
        if event.result.startswith('放行'):
            return 'runtime_failed' if '识别异常' in event.result else None
        return 'ad_block' if ('垃圾消息' in event.result or '垃圾信息' in event.result) else 'runtime_failed'
    if event.event_type == '入群验证':
        if '验证通过' in event.result: return 'verification_passed'
        if '验证超时' in event.result: return 'verification_timeout'
        if '回答错误' in event.result: return 'verification_wrong'
    if event.event_type == '群广播' and event.result.startswith('发送成功'):
        return 'broadcast_success'
    if event.event_type == '群广播' and any(word in event.result for word in ('发送失败', '发送结果不确定', '执行中断')):
        return 'broadcast_failed'
    if event.event_type == '连接异常': return 'connection_failed'
    if event.event_type == '连接恢复': return 'connection_restored'
    if event.event_type in ('连接检测', '连接监测'):
        return None
    if event.event_type == '自动停用': return 'runtime_failed'
    if event.event_type in ('运行异常', '机器人运行'): return 'runtime_failed'
    return None


def compact_text(value, limit=120):
    text = re.sub(r'\s+', ' ', str(value or '')).strip().rstrip('。，；; ')
    return text if len(text) <= limit else text[:limit - 1] + '…'


def notification_body(event, key):
    """推送只给一行摘要，详细消息留在事件日志。"""
    result = compact_text(event.result, 110)
    user = compact_text(event.nickname or (str(event.user_id) if getattr(event, 'user_id', None) is not None else '成员'), 24)
    if key == 'connection_failed':
        body = 'Telegram 连接中断，正在检测重试'
    elif key == 'connection_restored':
        body = result
    elif key == 'runtime_failed':
        subject = 'Telegram 连续重试 3 次失败' if event.content == '超过最大重试次数' else compact_text(event.content, 45)
        body = f'{subject}，{result}'
    elif key == 'broadcast_success':
        body = '群广播发送成功'
    elif key == 'broadcast_failed':
        body = f'群广播{result}' if result.startswith('发送') else f'群广播异常，{result}'
    elif key == 'verification_passed':
        body = f'{user}已通过入群验证'
    elif key in ('verification_timeout', 'verification_wrong'):
        body = f'{user}{EVENT_OPTIONS[key]}，{result}'
    else:
        body = f'{EVENT_OPTIONS[key]}，{user}，{result}'
    return compact_text(body, 160)


@post_save(EventLog)
async def enqueue_event(sender, instance, created, using_db, update_fields):
    if not created:
        return
    key = event_key(instance)
    if not key:
        return
    settings = await PushSettings.get_or_none(id=1)
    if not settings or settings.provider != 'bark' or not settings.bark_url:
        return
    if key not in settings.events:
        return
    body = notification_body(instance, key)
    async with in_transaction():
        job, queued = await PushJob.get_or_create(event_id=instance.id, defaults={
            'event_key': key, 'config_revision': settings.revision, 'title': BARK_TITLE,
            'body': body, 'expires_at': time.time() + 3600,
        })
        if queued:
            await record_push_result(job, '推送中')


def send_bark(url, title, body):
    request = Request(url, data=json.dumps({'title': BARK_TITLE, 'body': compact_text(body, 160), 'group': BARK_GROUP}, ensure_ascii=False).encode(),
                      headers={'Content-Type': 'application/json'}, method='POST')
    try:
        with open_request(request, direct=True, timeout=10) as response:
            raw = response.read(65537)
        if len(raw) > 65536:
            raise PushError('Bark 返回内容过大', uncertain=True)
        result = json.loads(raw)
        if not isinstance(result, dict) or result.get('code') != 200:
            raise PushError('Bark 拒绝推送请检查地址和设备 Key')
    except HTTPError as error:
        raise PushError('Bark 服务请求失败', retryable=error.code == 429, uncertain=error.code >= 500) from None
    except URLError as error:
        safe = isinstance(error.reason, (ConnectionRefusedError, socket.gaierror))
        raise PushError('Bark 直连失败请检查推送地址和服务器网络', retryable=safe, uncertain=not safe) from None
    except (TimeoutError, OSError):
        raise PushError('Bark 连接超时或中断', uncertain=True) from None
    except (ValueError, TypeError):
        raise PushError('Bark 返回格式不正确', uncertain=True) from None


async def push_log_event(job):
    source = await EventLog.get_or_none(id=job.event_id)
    if source and source.event_type == '运行异常':
        return '推送运行异常'
    return '推送' + EVENT_OPTIONS.get(job.event_key, '系统事件')


async def record_push_result(job, result):
    # 入队时占位，后续只更新状态；不因网络响应较慢改变日志位置。
    existing = await EventLog.filter(event_type='系统推送', parent_event_id=job.event_id).first()
    if existing:
        await EventLog.filter(id=existing.id).update(result=result)
        return existing
    source = await EventLog.get_or_none(id=job.event_id)
    if not source:
        # 用户已清空原日志时，不把旧推送任务重新带回日志表。
        return None
    return await record_event(event_type='系统推送', content=await push_log_event(job), result=result,
                              flow_id=source.flow_id, parent_event_id=job.event_id)


async def push_tick():
    now = time.time()
    for job in await PushJob.filter(state='sending', started_at__lt=now - 120):
        async with in_transaction():
            if await PushJob.filter(id=job.id, state='sending').update(state='unknown'):
                await record_push_result(job, '推送结果不确定 过程被中断 未重发')
    jobs = await PushJob.filter(state='queued', available_at__lte=now).order_by('id').limit(10)
    for job in jobs:
        settings = await PushSettings.get_or_none(id=1)
        if not settings or settings.provider != 'bark' or settings.revision != job.config_revision or job.event_key not in settings.events or job.expires_at <= now:
            changed = await PushJob.filter(id=job.id, state='queued').update(state='cancelled')
            if changed:
                await record_push_result(job, '推送已取消 设置已变更或任务已过期')
            continue
        event = await EventLog.get_or_none(id=job.event_id)
        if event and job.event_key == 'connection_failed' and event_key(event) is None:
            changed = await PushJob.filter(id=job.id, state='queued').update(state='cancelled')
            if changed:
                await record_push_result(job, '推送已取消 设置已变更或任务已过期')
            continue
        if not await PushJob.filter(id=job.id, state='queued').update(state='sending', started_at=time.time()):
            continue
        try:
            await run_in_threadpool(send_bark, settings.bark_url, job.title, job.body)
        except PushError as error:
            attempts = job.attempts + 1
            if error.retryable and not error.uncertain and attempts < 3:
                await PushJob.filter(id=job.id).update(state='queued', attempts=attempts, available_at=time.time() + attempts * 10)
                await record_push_result(job, f'推送重试中 第 {attempts} 次失败')
            else:
                async with in_transaction():
                    await PushJob.filter(id=job.id).update(state='unknown' if error.uncertain else 'failed', attempts=attempts)
                    await record_push_result(job, ('推送结果不确定 未重发 ' if error.uncertain else '推送失败 ') + str(error))
        else:
            async with in_transaction():
                await PushJob.filter(id=job.id).update(state='done', attempts=job.attempts + 1)
                await record_push_result(job, '推送成功')


async def run_push_worker():
    path = DATABASE_PATH.parent / 'push-worker.lock'
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('a') as lock:
        while True:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                await asyncio.sleep(2)
        last_cleanup = 0
        while True:
            await push_tick()
            if time.monotonic() - last_cleanup >= 60:
                await PushJob.filter(state__in=['done', 'failed', 'unknown', 'cancelled'], expires_at__lt=time.time() - 86400).delete()
                last_cleanup = time.monotonic()
            await asyncio.sleep(1)
