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
