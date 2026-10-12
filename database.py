from pathlib import Path

from tortoise import Tortoise

from models import Administrator


BASE_DIR = Path(__file__).resolve().parent
DATABASE_PATH = BASE_DIR / "data" / "titguard.db"


# 启动时连接数据库。
async def init_db():
    DATABASE_PATH.parent.mkdir(parents=True, exist_ok=True)

    await Tortoise.init(
        db_url=f"sqlite://{DATABASE_PATH.as_posix()}",
        modules={
            "models": ["models"],
        },
        use_tz=False,
        timezone="UTC",
    )

    # 仅创建不存在的表，不清空已有数据。
    await Tortoise.generate_schemas(safe=True)


# 程序关闭时释放数据库连接。
async def close_db():
    await Tortoise.close_connections()


# 检查是否已经有管理员。
async def has_admin() -> bool:
    return await Administrator.exists()


# 创建管理员。
async def create_admin(username: str, password_hash: str):
    return await Administrator.create(
        id=1,
        username=username,
        password_hash=password_hash,
    )