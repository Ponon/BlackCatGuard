"""真实群事件计数，不统计消息测试，不依赖事件日志是否被清空。"""
import hashlib
import time
from datetime import datetime, timedelta
from dao.models import GroupActivity
from utils.dates import BEIJING_TIME


async def record_activity(bot_key, chat_id, kind, identity, occurred_at=None):
    event_id = hashlib.sha256(f'{bot_key}:{chat_id}:{kind}:{identity}'.encode()).hexdigest()
    await GroupActivity.get_or_create(id=event_id, defaults={
        'bot_key': bot_key, 'chat_id': chat_id, 'kind': kind,
        'occurred_at': occurred_at if occurred_at is not None else time.time(),
    })


async def today_counts(bot_key, chat_id):
    midnight = datetime.now(BEIJING_TIME).replace(hour=0, minute=0, second=0, microsecond=0)
    query = GroupActivity.filter(bot_key=bot_key, chat_id=chat_id,
        occurred_at__gte=midnight.timestamp(), occurred_at__lt=(midnight + timedelta(days=1)).timestamp())
    return await query.filter(kind='join').count(), await query.filter(kind='blocked').count()
