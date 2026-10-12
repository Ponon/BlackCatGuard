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
    timeout_action = fields.CharField(max_length=16, default="kick")
    wrong_action = fields.CharField(max_length=16, default="none")

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


# 旧版单条广播表，仅供迁移到内容库和循环任务使用。
class BroadcastSettings(Model):
    id = fields.IntField(pk=True)
    enabled = fields.BooleanField(default=False)
    message = fields.TextField(default='')
    buttons = fields.JSONField(default=list)
    schedule_mode = fields.CharField(max_length=16, default='interval')
    interval_minutes = fields.IntField(default=60)
    send_time = fields.CharField(max_length=5, default='09:00')
    weekdays = fields.JSONField(default=lambda: [0, 1, 2, 3, 4, 5, 6])

    class Meta:
        table = 'broadcast_settings'


# 内容库没有启用状态，可被多条循环任务复用。
class BroadcastContent(Model):
    id = fields.IntField(pk=True)
    name = fields.CharField(max_length=64)
    message = fields.TextField()
    buttons = fields.JSONField(default=list)

    class Meta:
        table = 'broadcast_contents'


# 只有循环任务有开关；每条任务必须绑定已保存的内容。
class BroadcastTask(Model):
    id = fields.IntField(pk=True)
    # 兼容已有表的非空列，新任务不再维护独立名称。
    name = fields.CharField(max_length=64, default='')
    content = fields.ForeignKeyField('models.BroadcastContent', related_name='tasks', on_delete=fields.RESTRICT)
    enabled = fields.BooleanField(default=False)
    schedule_mode = fields.CharField(max_length=16, default='daily')
    interval_minutes = fields.IntField(default=60)
    times = fields.JSONField(default=lambda: ['09:00'])
    weekdays = fields.JSONField(default=lambda: list(range(7)))

    class Meta:
        table = 'broadcast_tasks'


class BroadcastSchedule(Model):
    """持久化调度进度，重启后不重复执行已经领取的时间点。"""
    task = fields.OneToOneField('models.BroadcastTask', related_name='schedule', on_delete=fields.CASCADE)
    fingerprint = fields.TextField()
    next_due = fields.FloatField()

    class Meta:
        table = 'broadcast_schedules'


class TelegramCursor(Model):
    """按机器人凭据摘要保存接收进度，不保存 Token。"""
    bot_key = fields.CharField(max_length=64, pk=True)
    offset = fields.BigIntField(default=0)

    class Meta:
        table = 'telegram_cursors'


class JoinSession(Model):
    """验证与欢迎流程持久化，重启后继续处理超时和消息删除。"""
    id = fields.CharField(max_length=32, pk=True)
    bot_key = fields.CharField(max_length=64)
    chat_id = fields.CharField(max_length=32)
    user_id = fields.BigIntField()
    user = fields.JSONField()
    answer = fields.CharField(max_length=16, default='ok')
    question = fields.TextField(default='')
    choices = fields.JSONField(default=list)
    permissions = fields.JSONField(default=dict)
    deadline = fields.FloatField(default=0)
    message_id = fields.BigIntField(null=True)
    state = fields.CharField(max_length=24, default='new')
    delete_at = fields.FloatField(default=0)
    failure = fields.TextField(default='')
    failure_action = fields.CharField(max_length=16, default='')
    failure_reason = fields.CharField(max_length=32, default='')
    config_revision = fields.CharField(max_length=32, default='')
    retry_at = fields.FloatField(default=0)
    failure_count = fields.IntField(default=0)

    class Meta:
        table = 'join_sessions'


class RuntimeHealth(Model):
    """后台探测结果与熔断状态，不使用历史手动检测冒充在线状态。"""
    id = fields.IntField(pk=True)
    config_key = fields.CharField(max_length=64)
    state = fields.CharField(max_length=16, default='checking')
    failures = fields.IntField(default=0)
    checked_at = fields.FloatField(default=0)
    latency_ms = fields.IntField(null=True)
    last_success = fields.FloatField(default=0)
    stopped = fields.BooleanField(default=False)
    reason = fields.CharField(max_length=500, default='')

    class Meta:
        table = 'runtime_health'


class BroadcastAttempt(Model):
    """发送前先落库，发送结果不确定时不重复投递。"""
    id = fields.CharField(max_length=64, pk=True)
    task_id = fields.IntField()
    state = fields.CharField(max_length=16, default='sending')
    started_at = fields.FloatField()
    content = fields.TextField(default='')

    class Meta:
        table = 'broadcast_attempts'


class ReceiverFault(Model):
    """接收程序冲突等运行错误也参与后台健康判断。"""
    id = fields.IntField(pk=True)
    config_key = fields.CharField(max_length=64)
    reason = fields.CharField(max_length=500)

    class Meta:
        table = 'receiver_faults'


class ModerationSettings(Model):
    id = fields.IntField(pk=True)
    message_action = fields.CharField(max_length=16, default='single')
    restrict_user = fields.BooleanField(default=False)
    kick_user = fields.BooleanField(default=False)


class ObservedMessage(Model):
    id = fields.CharField(max_length=64, pk=True)
    bot_key = fields.CharField(max_length=64)
    chat_id = fields.CharField(max_length=32)
    user_id = fields.BigIntField(db_index=True)
    message_id = fields.BigIntField()
    sent_at = fields.FloatField(db_index=True)


class ModerationJob(Model):
    """消息判断与删除进度独立于事件接收，避免 AI 请求阻塞入群验证。"""
    id = fields.CharField(max_length=64, pk=True)
    bot_key = fields.CharField(max_length=64)
    chat_id = fields.CharField(max_length=32)
    message_id = fields.BigIntField()
    update_id = fields.BigIntField()
    user = fields.JSONField()
    content = fields.TextField()
    state = fields.CharField(max_length=16, default='queued', db_index=True)
    result = fields.TextField(default='')
    rule_key = fields.CharField(max_length=64, default='')
    operations = fields.JSONField(default=list)
    completed = fields.JSONField(default=list)
    attempts = fields.IntField(default=0)
    available_at = fields.FloatField(default=0)
    expires_at = fields.FloatField()

    class Meta:
        table = 'moderation_jobs'


class PushSettings(Model):
    id = fields.IntField(pk=True)
    provider = fields.CharField(max_length=16, default='')
    bark_url = fields.CharField(max_length=2048, default='')
    events = fields.JSONField(default=list)
    revision = fields.CharField(max_length=32)

    class Meta:
        table = 'push_settings'


class PushJob(Model):
    """通知先入队，网络故障不阻塞群消息处理。"""
    id = fields.IntField(pk=True)
    event_id = fields.IntField(unique=True)
    event_key = fields.CharField(max_length=32)
    config_revision = fields.CharField(max_length=32)
    title = fields.CharField(max_length=128)
    body = fields.TextField()
    state = fields.CharField(max_length=16, default='queued', db_index=True)
    attempts = fields.IntField(default=0)
    available_at = fields.FloatField(default=0)
    started_at = fields.FloatField(default=0)
    expires_at = fields.FloatField()

    class Meta:
        table = 'push_jobs'


class GroupActivity(Model):
    """首页真实事件统计独立于可清空的操作日志，按事件幂等保存。"""
    id = fields.CharField(max_length=64, pk=True)
    bot_key = fields.CharField(max_length=64)
    chat_id = fields.CharField(max_length=32)
    kind = fields.CharField(max_length=16)
    occurred_at = fields.FloatField(db_index=True)

    class Meta:
        indexes = (('bot_key', 'chat_id', 'kind', 'occurred_at'),)
