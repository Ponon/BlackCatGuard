"""广播后台调度：北京时间每日定时，或从启用后按间隔执行。"""
from services.event_logs import record_event
import asyncio
import json
import hashlib
import time
from datetime import datetime, timedelta

from dao.models import BroadcastTask, BroadcastSchedule, BroadcastAttempt, EventLog
from tortoise.transactions import in_transaction
from services.broadcast_delivery import deliver_broadcast
from utils.dates import BEIJING_TIME


def rule_key(task):
    return json.dumps([task.schedule_mode, task.interval_minutes, task.times], separators=(',', ':'))


def next_due(task, now):
    """只安排未来时间，停机错过的广播不补发。"""
    if task.schedule_mode == 'interval':
        return now + task.interval_minutes * 60
    current = datetime.fromtimestamp(now, BEIJING_TIME)
    candidates = []
    for value in task.times:
        hour, minute = map(int, value.split(':'))
        candidate = current.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if candidate.timestamp() <= now:
            candidate += timedelta(days=1)
        candidates.append(candidate.timestamp())
    return min(candidates)


async def tick(now=None):
    now = now if now is not None else datetime.now(BEIJING_TIME).timestamp()
    # 中断的发送不能直接重发，单独记录为结果未知。
    for attempt in await BroadcastAttempt.filter(state__in=['queued', 'sending'], started_at__lt=time.time() - 120):
        async with in_transaction():
            changed = await BroadcastAttempt.filter(id=attempt.id, state=attempt.state).update(state='unknown')
            if changed:
                result = f'任务 {attempt.task_id} 上次执行中断 发送结果不确定 未自动重发' if attempt.state == 'sending' else f'任务 {attempt.task_id} 发送前执行中断 已跳过且不补发'
                await record_event(flow_id=f'broadcast:{attempt.id}', event_type='群广播', content=attempt.content, result=result)
    # 关闭后清除进度，再开启从新的启用时间计时。
    disabled_ids = await BroadcastTask.filter(enabled=False).values_list('id', flat=True)
    if disabled_ids:
        await BroadcastSchedule.filter(task_id__in=disabled_ids).delete()
    for task in await BroadcastTask.filter(enabled=True):
        key = rule_key(task)
        progress, created = await BroadcastSchedule.get_or_create(
            task_id=task.id, defaults={'fingerprint': key, 'next_due': next_due(task, now)},
        )
        if created:
            continue
        if progress.fingerprint != key:
            await BroadcastSchedule.filter(id=progress.id, fingerprint=progress.fingerprint).update(
                fingerprint=key, next_due=next_due(task, now),
            )
            continue
        if progress.next_due > now:
            continue
        # 超过一分钟的时间点视为停机遗漏；不集中补发历史消息。
        attempt_id = hashlib.sha256(f'{progress.id}:{progress.next_due}'.encode()).hexdigest()
        async with in_transaction():
            claimed = await BroadcastSchedule.filter(
                id=progress.id, fingerprint=key, next_due=progress.next_due,
            ).update(next_due=next_due(task, now))
            if claimed:
                await BroadcastAttempt.get_or_create(id=attempt_id, defaults={'task_id': task.id, 'state': 'queued', 'started_at': time.time()})
        if claimed:
            if now - progress.next_due < 60:
                await deliver_broadcast(task.id, attempt_id=attempt_id)
            else:
                await BroadcastAttempt.filter(id=attempt_id).update(state='skipped')
                await EventLog.create(event_type='群广播', content=f'任务 {task.id}', result='错过计划发送时间 已跳过且不补发')



async def run_scheduler():
    while True:
        # 由后台监督器记录异常并重启，持久化发送进度避免重复广播。
        await tick()
        await asyncio.sleep(1)
