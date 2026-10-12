from datetime import datetime
from zoneinfo import ZoneInfo

from tortoise import Model, fields
from tortoise.timezone import get_use_tz


# 管理员表
class Administrator(Model):
    id = fields.IntField(pk=True, description="管理员 ID")
    username = fields.CharField(max_length=32, unique=True, description="管理员账号")
    password_hash = fields.TextField(description="密码哈希")
    created_at = fields.DatetimeField(auto_now_add=True, description="创建时间")

    class Meta:
        table = "administrators"
        table_description = "管理员表"


# 单机器人、单群配置，固定使用 ID=1。
class GroupConfig(Model):
    id = fields.IntField(pk=True)
    bot_token = fields.CharField(max_length=256, description="机器人凭据，仅服务端使用")
    chat_id = fields.CharField(max_length=32, description="目标群数字 ID")
    revision = fields.CharField(max_length=32, description="配置版本，防止旧检测结果覆盖新配置")
    check_result = fields.JSONField(default=dict, description="最近一次检测结果")

    class Meta:
        table = "group_config"


# 全局网络配置，空地址表示沿用系统默认网络设置。
class SystemSettings(Model):
    id = fields.IntField(pk=True)
    proxy_url = fields.CharField(max_length=2048, default="", description="HTTP/HTTPS 代理地址")

    class Meta:
        table = "system_settings"


# 入群验证独立配置，关闭功能时保留用户选择的验证方式。
class VerificationSettings(Model):
    id = fields.IntField(pk=True)
    enabled = fields.BooleanField(default=False)
    method = fields.CharField(max_length=16, default="button")
    timeout_seconds = fields.IntField(default=60)

    class Meta:
        table = "verification_settings"


# 欢迎语独立于验证开关，验证关闭时也可单独启用。
class WelcomeSettings(Model):
    id = fields.IntField(pk=True)
    enabled = fields.BooleanField(default=False)
    message = fields.TextField(default="欢迎 {nickname}（{username}）加入群组！")
    delete_after_seconds = fields.IntField(default=60)

    class Meta:
        table = "welcome_settings"


class KeywordSettings(Model):
    id = fields.IntField(pk=True)
    enabled = fields.BooleanField(default=False)
    keywords = fields.JSONField(default=list)

    class Meta:
        table = "keyword_settings"


class AISettings(Model):
    id = fields.IntField(pk=True)
    enabled = fields.BooleanField(default=False)
    base_url = fields.CharField(max_length=2048, default="")
    model = fields.CharField(max_length=128, default="")
    api_key = fields.TextField(default="")

    class Meta:
        table = "ai_settings"


# 白名单先于关键词和 AI 判断，关闭拦截时仍保留配置。
class WhitelistSettings(Model):
    id = fields.IntField(pk=True)
    entries = fields.JSONField(default=list)

    class Meta:
        table = "whitelist_settings"


def event_time_now():
    # 现有数据库使用无时区时间；日志统一保存北京时间，避免依赖服务器时区。
    value = datetime.now(ZoneInfo("Asia/Shanghai"))
    return value if get_use_tz() else value.replace(tzinfo=None)


# 保存事件内容与处理结果，配置密钥不写入日志。
class EventLog(Model):
    id = fields.IntField(pk=True)
    created_at = fields.DatetimeField(default=event_time_now)
    event_type = fields.CharField(max_length=32)
    nickname = fields.CharField(max_length=128, default="")
    username = fields.CharField(max_length=64, default="")
    content = fields.TextField(default="", description="事件消息内容")
    result = fields.CharField(max_length=500)

    class Meta:
        table = "event_logs"
