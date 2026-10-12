from tortoise import fields
from tortoise.models import Model


class Administrator(Model):
    id = fields.IntField(primary_key=True)

    username = fields.CharField(
        max_length=32,
        unique=True,
    )

    password_hash = fields.TextField()

    created_at = fields.DatetimeField(
        auto_now_add=True,
    )

    class Meta:
        # 使用原来的表名，避免另外创建一张表。
        table = "administrators"