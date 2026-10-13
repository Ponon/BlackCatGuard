"""按北京时间清理过期日志 0 表示永久保留 不影响业务配置"""
import asyncio
from datetime import timedelta
from dao.models import EventLog, SystemSettings, event_time_now


async def cleanup_logs():
    settings = await SystemSettings.get_or_none(id=1)
    days = settings.log_retention_days if settings else 30
    if days <= 0:
        return 0
    return await EventLog.filter(created_at__lt=event_time_now() - timedelta(days=days)).delete()


async def run_log_cleanup():
    while True:
        await cleanup_logs()
        await asyncio.sleep(24 * 60 * 60)
