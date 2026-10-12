"""各页面共用的群配置与功能启用判断。"""
from dao.models import GroupConfig


async def group_is_configured(*, lock=False):
    query = GroupConfig.filter(id=1).exclude(bot_token='').exclude(chat_id='')
    if lock:
        query = query.select_for_update()
    return await query.exists()


def verification_is_enabled(settings, configured):
    return bool(
        configured and settings and settings.enabled
        and settings.method in ('button', 'arithmetic')
        and 10 <= settings.timeout_seconds <= 3600
        and settings.timeout_action in ('kick', 'restrict')
        and settings.wrong_action in ('none', 'kick', 'restrict')
    )


def welcome_is_enabled(settings, configured):
    return bool(
        configured and settings and settings.enabled and settings.message.strip()
        and 0 <= settings.delete_after_seconds <= 86400
    )


def keyword_is_enabled(settings, configured):
    return bool(configured and settings and settings.enabled and settings.keywords)


def ai_is_enabled(settings, configured):
    return bool(configured and settings and settings.enabled and settings.base_url and settings.model)
