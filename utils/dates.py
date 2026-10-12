"""网页统一使用北京时间；兼容已有无时区的北京时间记录。"""
from datetime import datetime
from zoneinfo import ZoneInfo

BEIJING_TIME = ZoneInfo('Asia/Shanghai')


def display_time(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=BEIJING_TIME)
    return value.astimezone(BEIJING_TIME).strftime('%Y-%m-%d %H:%M:%S')
