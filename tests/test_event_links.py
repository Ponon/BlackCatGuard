"""旧数据库关联字段迁移保留原日志且可重复执行。"""
import unittest
from tortoise import Tortoise
from dao.database import migrate_event_links


class EventLinkMigrationTests(unittest.IsolatedAsyncioTestCase):
    async def test_additive_migration_preserves_existing_records(self):
        await Tortoise.init(db_url='sqlite://:memory:', modules={'models': ['dao.models']})
        try:
            db = Tortoise.get_connection('default')
            await db.execute_query('CREATE TABLE event_logs (id INTEGER PRIMARY KEY, content TEXT)')
            await db.execute_query("INSERT INTO event_logs (id, content) VALUES (1, '原日志')")
            await db.execute_query('CREATE TABLE runtime_health (id INTEGER PRIMARY KEY, reason TEXT)')
            await db.execute_query("INSERT INTO runtime_health (id, reason) VALUES (1, '原状态')")
            await migrate_event_links()
            await migrate_event_links()
            logs = await db.execute_query_dict('SELECT * FROM event_logs')
            self.assertEqual(logs, [{'id': 1, 'content': '原日志', 'flow_id': '', 'parent_event_id': None}])
            health = await db.execute_query_dict('SELECT * FROM runtime_health')
            self.assertEqual(health, [{'id': 1, 'reason': '原状态', 'flow_id': ''}])
        finally:
            await Tortoise.close_connections()
