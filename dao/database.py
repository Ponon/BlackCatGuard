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
    await migrate_event_logs()
    await migrate_broadcast_settings()
    await migrate_daily_broadcast_tasks()
    await migrate_moderation_jobs()
    await migrate_join_actions()
    await migrate_runtime_latency()
    await migrate_event_links()
    await migrate_push_types()


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


async def migrate_event_logs():
    """为已有日志表补内容列，旧日志内容留空。"""
    connection = Tortoise.get_connection("default")
    columns = await connection.execute_query_dict('PRAGMA table_info("event_logs")')
    if "content" not in {column["name"] for column in columns}:
        await connection.execute_query("ALTER TABLE event_logs ADD COLUMN content TEXT NOT NULL DEFAULT ''")


async def migrate_broadcast_settings():
    """将旧单条广播完整迁移为内容与循环任务，事务成功后移除旧记录。"""
    from tortoise.transactions import in_transaction
    from dao.models import BroadcastSettings, BroadcastContent, BroadcastTask, GroupConfig

    async with in_transaction():
        old = await BroadcastSettings.get_or_none(id=1)
        if old is None:
            return
        content = await BroadcastContent.create(name='原广播内容', message=old.message, buttons=old.buttons)
        configured = await GroupConfig.filter(id=1).exclude(bot_token='').exclude(chat_id='').exists()
        await BroadcastTask.create(
            content=content,
            enabled=bool(configured and old.enabled and old.message.strip()),
            schedule_mode=old.schedule_mode, interval_minutes=old.interval_minutes,
            times=[old.send_time], weekdays=old.weekdays,
        )
        await old.delete()


async def migrate_daily_broadcast_tasks():
    """移除每周模式：保留内容和时间点，转成关闭的每日任务供用户重新启用。"""
    from dao.models import BroadcastTask
    await BroadcastTask.filter(schedule_mode='weekly').update(schedule_mode='daily', enabled=False)


async def migrate_moderation_jobs():
    """已有消息队列补规则摘要，旧判断不会直接用于新规则下的删除。"""
    connection = Tortoise.get_connection('default')
    columns = await connection.execute_query_dict('PRAGMA table_info("moderation_jobs")')
    names = {column['name'] for column in columns}
    for name in ('operations', 'completed'):
        if columns and name not in names:
            await connection.execute_query(f"ALTER TABLE moderation_jobs ADD COLUMN {name} JSON NOT NULL DEFAULT '[]'")
    if columns and 'rule_key' not in {column['name'] for column in columns}:
        await connection.execute_query("ALTER TABLE moderation_jobs ADD COLUMN rule_key VARCHAR(64) NOT NULL DEFAULT ''")


async def migrate_join_actions():
    connection = Tortoise.get_connection('default')
    for table, additions in {
        'verification_settings': {'timeout_action': "VARCHAR(16) NOT NULL DEFAULT 'kick'", 'wrong_action': "VARCHAR(16) NOT NULL DEFAULT 'none'"},
        'join_sessions': {'failure_action': "VARCHAR(16) NOT NULL DEFAULT ''", 'failure_reason': "VARCHAR(32) NOT NULL DEFAULT ''", 'retry_at': 'REAL NOT NULL DEFAULT 0', 'failure_count': 'INT NOT NULL DEFAULT 0', 'config_revision': "VARCHAR(32) NOT NULL DEFAULT ''"},
    }.items():
        columns = await connection.execute_query_dict(f'PRAGMA table_info("{table}")')
        names = {column['name'] for column in columns}
        for name, definition in additions.items():
            if columns and name not in names:
                await connection.execute_query(f'ALTER TABLE "{table}" ADD COLUMN "{name}" {definition}')

    # 旧版「允许重新作答」迁移为不执行额外处罚，保留原有验证流程。
    await connection.execute_query("UPDATE verification_settings SET wrong_action = 'none' WHERE wrong_action = 'retry'")


async def migrate_runtime_latency():
    connection = Tortoise.get_connection('default')
    columns = await connection.execute_query_dict('PRAGMA table_info("runtime_health")')
    if columns and 'latency_ms' not in {column['name'] for column in columns}:
        await connection.execute_query('ALTER TABLE runtime_health ADD COLUMN latency_ms INT NULL')


async def migrate_event_links():
    connection = Tortoise.get_connection('default')
    for table, additions in {
        'event_logs': {'flow_id': "VARCHAR(128) NOT NULL DEFAULT ''", 'parent_event_id': 'INT NULL'},
        'runtime_health': {'flow_id': "VARCHAR(128) NOT NULL DEFAULT ''"},
    }.items():
        columns = await connection.execute_query_dict(f'PRAGMA table_info("{table}")')
        names = {column['name'] for column in columns}
        for name, definition in additions.items():
            if columns and name not in names:
                await connection.execute_query(f'ALTER TABLE "{table}" ADD COLUMN "{name}" {definition}')
    await connection.execute_query('CREATE INDEX IF NOT EXISTS event_logs_flow_id_idx ON event_logs (flow_id)')


async def migrate_push_types():
    from dao.models import PushSettings, PushJob
    for settings in await PushSettings.all():
        if 'auto_disabled' in settings.events:
            settings.events = list(dict.fromkeys('runtime_failed' if value == 'auto_disabled' else value for value in settings.events))
            await settings.save(update_fields=['events'])
    await PushJob.filter(event_key='auto_disabled').update(event_key='runtime_failed')
