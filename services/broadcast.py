"""广播配置的规则说明与预览；本模块不发送群消息。"""
from datetime import datetime
from utils.dates import BEIJING_TIME

def schedule_description(mode, interval_minutes, times):
    if mode == 'interval':
        return f'每隔 {interval_minutes} 分钟发送一次'
    time_text = '、'.join(times)
    if mode == 'daily':
        return f'每天 {time_text} 发送'
    return '循环规则需要重新设置'


def preview_message(message, now=None):
    now = now or datetime.now(BEIJING_TIME)
    return message.replace('{datetime}', now.strftime('%Y-%m-%d %H:%M:%S'))
