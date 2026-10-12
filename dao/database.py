from pathlib import Path

from tortoise import Tortoise


BASE_DIR = Path(__file__).resolve().parent.parent
DATABASE_PATH = BASE_DIR / "data" / "blackcatguard.db"


# 启动时连接数据库。
async def init_db():
    DATABASE_PATH.parent.mkdir(parents=True, exist_ok=True)

    await Tortoise.init(
        db_url=f"sqlite://{DATABASE_PATH.as_posix()}",
        modules={
            "models": ["dao.models"],
        },
        use_tz=False,
        timezone="UTC",
        # 让网页请求任务也能使用启动时建立的数据库上下文。
        _enable_global_fallback=True,
    )

    # 仅创建不存在的表，不清空已有数据。
    await Tortoise.generate_schemas(safe=True)


# 程序关闭时释放数据库连接。
async def close_db():
    await Tortoise.close_connections()
