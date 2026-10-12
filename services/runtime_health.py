"""独立后台健康监测，单次失败不关闭功能，连续三次失败自动停用。"""
import asyncio
import fcntl
import hashlib
import logging
import time

from starlette.concurrency import run_in_threadpool
from tortoise.transactions import in_transaction
from dao.database import DATABASE_PATH
from dao.models import (GroupConfig, RuntimeHealth, VerificationSettings, WelcomeSettings,
                        KeywordSettings, AISettings, BroadcastTask, BroadcastSettings, BroadcastSchedule, EventLog, ReceiverFault)
from services.network import get_proxy_url
from services.telegram import inspect_group

logger = logging.getLogger(__name__)
INTERVAL = 10
STALE_AFTER = 45
SLOW_AFTER_MS = 1000


def config_key(group, proxy):
    return hashlib.sha256(f'{group.revision}:{group.bot_token}:{group.chat_id}:{proxy}'.encode()).hexdigest()


async def current_health(group=None):
    group = group or await GroupConfig.get_or_none(id=1)
    if not group or not group.bot_token or not group.chat_id:
        return None
    health = await RuntimeHealth.get_or_none(id=1)
    if not health or health.config_key != config_key(group, await get_proxy_url()):
        return None
    return health


async def execution_allowed(group=None):
    current = await GroupConfig.get_or_none(id=1)
    if group and (not current or current.revision != group.revision or current.bot_token != group.bot_token or current.chat_id != group.chat_id):
        return False
    health = await current_health(current)
    return bool(health and not health.stopped and health.state in ('online', 'unstable')
                and health.last_success and time.time() - health.checked_at <= STALE_AFTER)


async def ensure_can_enable():
    from fastapi import HTTPException
    health = await current_health()
    if health and (health.stopped or time.time() - health.checked_at > STALE_AFTER):
        raise HTTPException(status_code=503, detail='连接异常请等待连接恢复后手动开启')


def present_health(state, label, health=None):
    # 只展示本轮成功且仍新鲜的延迟，失败或过期时清空旧测量值。
    latency = health.latency_ms if health and state == 'online' else None
    slow = latency is not None and latency >= SLOW_AFTER_MS
    tone = {'online': 'slow' if slow else 'good', 'unstable': 'warning',
            'offline': 'error', 'stale': 'error', 'checking': 'checking'}.get(state, 'idle')
    return {'state': state, 'label': label, 'failures': health.failures if health else 0,
            'checked_at': health.checked_at if health else 0, 'reason': health.reason if health else '',
            'latency_ms': latency, 'latency_label': f'{latency} ms' if latency is not None else '—',
            'tone': tone, 'connection_label': '延迟较高' if slow else label}


async def health_snapshot():
    group = await GroupConfig.get_or_none(id=1)
    if not group or not group.bot_token or not group.chat_id:
        return present_health('unconfigured', '未配置')
    health = await current_health(group)
    if not health:
        return present_health('checking', '正在检测')
    state = health.state
    if time.time() - health.checked_at > STALE_AFTER:
        state = 'stale'
    labels = {'online': '连接正常', 'unstable': f'连接波动 连续失败 {health.failures} 次',
              'offline': '连接异常 已关闭全部任务', 'checking': '正在检测', 'stale': '监测状态已过期 暂停执行'}
    return present_health(state, labels[state], health)


async def probe_once():
    group = await GroupConfig.get_or_none(id=1)
    if not group or not group.bot_token or not group.chat_id:
        return
    proxy = await get_proxy_url()
    key = config_key(group, proxy)
    # 每轮只探测一次，内部不重试，连续错误次数才有明确含义。
    result = await run_in_threadpool(inspect_group, group.bot_token, group.chat_id, proxy, request_timeout=3)
    receiver_fault = await ReceiverFault.get_or_none(id=1, config_key=key)
    if receiver_fault:
        result = {'ok': False, 'message': receiver_fault.reason}
    now = time.time()
    async with in_transaction():
        current = await GroupConfig.filter(id=1).select_for_update().first()
        if not current or config_key(current, await get_proxy_url()) != key:
            return
        health, _ = await RuntimeHealth.get_or_create(id=1, defaults={'config_key': key})
        if health.config_key != key:
            health.config_key, health.failures, health.stopped = key, 0, False
            health.last_success = 0
        was_failed = health.failures > 0
        if result.get('ok'):
            if was_failed:
                await EventLog.create(event_type='连接监测', content='Telegram 连接与权限检测恢复正常',
                    result='连接已恢复 已关闭的任务需手动开启' if health.stopped else '连接已恢复 保留现有任务状态')
            health.state, health.failures, health.stopped = 'online', 0, False
            health.last_success, health.reason = now, ''
        else:
            health.failures = min(health.failures + 1, 3)
            health.reason = str(result.get('message') or 'Telegram 连接或权限检测失败')[:500]
            health.state = 'unstable' if health.failures < 3 else 'offline'
            if not health.stopped:
                if health.failures == 3:
                    count = await BroadcastTask.filter(enabled=True).count()
                    for model in (VerificationSettings, WelcomeSettings, KeywordSettings, AISettings, BroadcastSettings):
                        await model.all().update(enabled=False)
                    await BroadcastTask.all().update(enabled=False)
                    await BroadcastSchedule.all().delete()
                    health.stopped = True
                    await EventLog.create(event_type='自动停用',
                        content=f'Telegram 连续三轮检测失败 原因 {health.reason}',
                        result=f'已关闭入群验证 欢迎语 关键词 AI 和 {count} 个广播任务 保留配置 恢复后需手动开启')
                else:
                    await EventLog.create(event_type='连接监测', content=health.reason,
                        result=f'连续失败 {health.failures} 次 暂不关闭任务')
        latency = result.get('latency_ms')
        health.latency_ms = latency if result.get('ok') and type(latency) is int and latency > 0 else None
        health.checked_at = now
        await health.save()


async def run_health_monitor():
    path = DATABASE_PATH.parent / 'health-monitor.lock'
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('a') as lock:
        while True:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                await asyncio.sleep(2)
        while True:
            started = time.monotonic()
            try:
                await probe_once()
            except Exception:
                logger.error('连接监测内部异常 暂不更新健康时间')
            await asyncio.sleep(max(1, INTERVAL - (time.monotonic() - started)))
