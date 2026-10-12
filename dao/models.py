from tortoise import Model, fields


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
