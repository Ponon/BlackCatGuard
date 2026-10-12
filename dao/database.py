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
    await migrate_verification_settings()
    await migrate_welcome_settings()


# 程序关闭时释放数据库连接。
async def close_db():
    await Tortoise.close_connections()


async def migrate_verification_settings():
    """兼容已有 SQLite 数据库：只补新增列，保留现有验证设置。"""
    connection = Tortoise.get_connection("default")
    columns = await connection.execute_query_dict('PRAGMA table_info("verification_settings")')
    existing = {column["name"] for column in columns}
    additions = {
        "welcome_message": "TEXT NOT NULL DEFAULT '欢迎 {nickname}（{username}）加入群组，请完成入群验证。'",
        "timeout_seconds": "INT NOT NULL DEFAULT 60",
        "delete_after_seconds": "INT NOT NULL DEFAULT 60",
    }
    for name, definition in additions.items():
        if name not in existing:
            await connection.execute_query(f'ALTER TABLE "verification_settings" ADD COLUMN "{name}" {definition}')


async def migrate_welcome_settings():
    """从早期合并配置迁移欢迎语；既有独立配置不覆盖。"""
    from dao.models import WelcomeSettings
    connection = Tortoise.get_connection("default")
    columns = await connection.execute_query_dict('PRAGMA table_info("verification_settings")')
    existing = {column["name"] for column in columns}
    if "welcome_message" not in existing or "delete_after_seconds" not in existing:
        return
    old_default = "欢迎 {nickname}（{username}）加入群组，请完成入群验证。"
    new_default = "欢迎 {nickname}（{username}）加入群组！"
    # 只修正旧默认文案，不覆盖用户自定义欢迎语。
    await WelcomeSettings.filter(message=old_default).update(message=new_default)
    rows = await connection.execute_query_dict('SELECT welcome_message, delete_after_seconds FROM verification_settings WHERE id=1')
    if rows:
        await WelcomeSettings.get_or_create(id=1, defaults={
            "enabled": False,
            "message": new_default if rows[0]["welcome_message"] == old_default else rows[0]["welcome_message"],
            "delete_after_seconds": rows[0]["delete_after_seconds"],
        })
