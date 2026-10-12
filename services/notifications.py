"""系统事件通知独立排队，推送故障不反向触发推送。"""
import asyncio
import fcntl
import json
import socket
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request

from starlette.concurrency import run_in_threadpool
from tortoise.signals import post_save
from tortoise.transactions import in_transaction
from dao.database import DATABASE_PATH
from dao.models import PushSettings, PushJob, EventLog
from services.network import open_request, get_proxy_url

EVENT_OPTIONS = {
    'ad_block': '广告拦截', 'verification_passed': '验证通过', 'verification_timeout': '验证超时', 'verification_wrong': '验证答错',
    'broadcast_failed': '广播失败', 'connection_failed': '连接异常', 'connection_restored': '连接恢复',
    'auto_disabled': '自动停用', 'runtime_failed': '运行异常',
}


class PushError(Exception):
    def __init__(self, message, *, retryable=False, uncertain=False):
        super().__init__(message)
        self.retryable, self.uncertain = retryable, uncertain


def event_key(event):
    if event.event_type == '广告拦截':
        return 'ad_block' if '垃圾消息' in event.result else 'runtime_failed'
    if event.event_type == '入群验证':
        if '验证通过' in event.result: return 'verification_passed'
        if '验证超时' in event.result: return 'verification_timeout'
        if '回答错误' in event.result: return 'verification_wrong'
    if event.event_type == '群广播' and not event.result.startswith('发送成功'):
        return 'broadcast_failed'
    if event.event_type == '连接监测':
        return 'connection_restored' if '连接已恢复' in event.result else 'connection_failed'
    if event.event_type == '自动停用': return 'auto_disabled'
    if event.event_type == '机器人运行': return 'runtime_failed'
    return None


@post_save(EventLog)
async def enqueue_event(sender, instance, created, using_db, update_fields):
    if not created:
        return
    key = event_key(instance)
    if not key:
        return
    settings = await PushSettings.get_or_none(id=1)
    if not settings or settings.provider != 'bark' or not settings.bark_url or key not in settings.events:
        return
    user = ' '.join(filter(None, [instance.nickname, instance.username]))
    body = '\n'.join(filter(None, [user, instance.content[:1000], instance.result]))[:1800]
    await PushJob.get_or_create(event_id=instance.id, defaults={
        'event_key': key, 'config_revision': settings.revision, 'title': '黑猫群管 ' + EVENT_OPTIONS[key],
        'body': body, 'expires_at': time.time() + 3600,
    })


def send_bark(url, title, body, proxy_url=''):
    request = Request(url, data=json.dumps({'title': title, 'body': body, 'group': '黑猫群管'}, ensure_ascii=False).encode(),
                      headers={'Content-Type': 'application/json'}, method='POST')
    try:
        with open_request(request, proxy_url=proxy_url, timeout=10) as response:
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
        raise PushError('Bark 连接失败请检查代理和网络', retryable=safe, uncertain=not safe) from None
    except (TimeoutError, OSError):
        raise PushError('Bark 连接超时或中断', uncertain=True) from None
    except (ValueError, TypeError):
        raise PushError('Bark 返回格式不正确', uncertain=True) from None


async def push_tick():
    now = time.time()
    for job in await PushJob.filter(state='sending', started_at__lt=now - 120):
        async with in_transaction():
            if await PushJob.filter(id=job.id, state='sending').update(state='unknown'):
                await EventLog.create(event_type='系统推送', content=job.title, result='推送过程被中断 结果不确定 未重发')
    jobs = await PushJob.filter(state='queued', available_at__lte=now).order_by('id').limit(10)
    for job in jobs:
        settings = await PushSettings.get_or_none(id=1)
        if not settings or settings.provider != 'bark' or settings.revision != job.config_revision or job.event_key not in settings.events or job.expires_at <= now:
            await PushJob.filter(id=job.id, state='queued').update(state='cancelled')
            continue
        if not await PushJob.filter(id=job.id, state='queued').update(state='sending', started_at=time.time()):
            continue
        try:
            await run_in_threadpool(send_bark, settings.bark_url, job.title, job.body, await get_proxy_url())
        except PushError as error:
            attempts = job.attempts + 1
            if error.retryable and not error.uncertain and attempts < 3:
                await PushJob.filter(id=job.id).update(state='queued', attempts=attempts, available_at=time.time() + attempts * 10)
            else:
                async with in_transaction():
                    await PushJob.filter(id=job.id).update(state='unknown' if error.uncertain else 'failed', attempts=attempts)
                    await EventLog.create(event_type='系统推送', content=job.title,
                        result=('推送结果不确定 未重发 ' if error.uncertain else '推送失败 ') + str(error))
        else:
            async with in_transaction():
                await PushJob.filter(id=job.id).update(state='done', attempts=job.attempts + 1)
                await EventLog.create(event_type='系统推送', content=job.title, result='Bark 推送服务已接收')


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
