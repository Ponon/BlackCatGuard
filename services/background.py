"""后台服务异常退出后重新启动，关闭应用时不重启任务。"""
import asyncio
import logging
import time
from dao.models import EventLog

logger = logging.getLogger(__name__)


async def supervise(service, name):
    last_report = float('-inf')
    while True:
        try:
            await service()
        except asyncio.CancelledError:
            raise
        except Exception:
            # 原始异常可能携带网络凭据，只输出固定服务名称。
            logger.error('%s 异常退出 五秒后重新启动', name)
            if time.monotonic() - last_report >= 60:
                try:
                    await EventLog.create(event_type='运行异常', content=f'{name} 后台服务异常退出', result='五秒后自动重新启动')
                except Exception:
                    logger.error('后台服务异常日志暂时无法写入')
                last_report = time.monotonic()
        await asyncio.sleep(5)
