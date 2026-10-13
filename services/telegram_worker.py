"""后台接收入群与验证按钮事件，无需公网回调地址。"""
import asyncio
import fcntl
import json
import logging

from dao.database import DATABASE_PATH
from dao.models import GroupConfig, TelegramCursor, ReceiverFault, EventLog
from services.join_runtime import bot_key, maintain, process_join, process_callback
from services.telegram import TelegramError, call_api
from services.runtime_health import execution_allowed, config_key, report_connection_fault
from services.network import get_proxy_url
from starlette.concurrency import run_in_threadpool
from services.moderation import enqueue_message

logger = logging.getLogger(__name__)


async def api(group, method, **params):
    return await run_in_threadpool(call_api, group.bot_token, method, proxy_url=await get_proxy_url(), **params)


async def poll_once(group, *, process_events=True):
    cursor, _ = await TelegramCursor.get_or_create(bot_key=bot_key(group.bot_token))
    updates = await api(group, 'getUpdates', offset=cursor.offset, timeout=2,
                        allowed_updates=json.dumps(['chat_member', 'callback_query', 'message', 'edited_message']))
    await ReceiverFault.filter(id=1, config_key=config_key(group, await get_proxy_url())).delete()
    if not process_events:
        return
    for update in updates:
        # 网络请求期间配置被更换时，旧机器人事件不再执行。
        current = await GroupConfig.get_or_none(id=1)
        if current is None or current.bot_token != group.bot_token or current.chat_id != group.chat_id:
            return
        if 'chat_member' in update:
            await process_join(group, update['chat_member'], update['update_id'])
        elif 'callback_query' in update:
            await process_callback(group, update['callback_query'])
        elif 'message' in update or 'edited_message' in update:
            await enqueue_message(group, update.get('message') or update['edited_message'], update['update_id'])
        cursor.offset = update['update_id'] + 1
        await cursor.save(update_fields=['offset'])


async def run_telegram_worker():
    # 同机多个应用进程只允许一个接收者，避免 getUpdates 冲突与重复处理。
    lock_path = DATABASE_PATH.parent / 'telegram-worker.lock'
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open('a') as lock:
        while True:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                await asyncio.sleep(2)
        previous_error = ''
        while True:
            group = await GroupConfig.get_or_none(id=1)
            if group and group.bot_token and group.chat_id:
                try:
                    # 即使接收更新失败，已有成员的验证超时仍继续处理。
                    allowed = await execution_allowed(group)
                    if allowed:
                        await maintain(group)
                    # 停用后仍只读检查事件接收是否恢复，不执行新成员任务。
                    await poll_once(group, process_events=allowed)
                    previous_error = ''
                except TelegramError as error:
                    text = str(error)
                    await ReceiverFault.update_or_create(id=1, defaults={
                        'config_key': config_key(group, await get_proxy_url()), 'reason': text[:500],
                    })
                    if not previous_error:
                        logger.error('Telegram 入群服务不可用 %s', text)
                        await report_connection_fault(group, await get_proxy_url(), text[:500])
                        previous_error = text
                    await asyncio.sleep(5)
                except Exception:
                    # 统一监督器记录异常并重启，不静默吞掉处理错误。
                    raise
            await asyncio.sleep(1)
