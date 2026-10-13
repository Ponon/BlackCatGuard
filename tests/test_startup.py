"""启动横幅按数据库启动前状态显示准确提示"""
import unittest
from unittest.mock import patch
from services.startup import log_startup, BANNER, local_access_url


class StartupTests(unittest.IsolatedAsyncioTestCase):
    async def test_new_database_banner(self):
        with self.assertLogs('uvicorn.error', level='INFO') as output:
            await log_startup(database_existed=False)
        text = '\n'.join(output.output)
        self.assertIn(BANNER, text)
        for value in ('项目名称：黑猫群管', '项目作者：Ponon', '项目地址：https://github.com/Ponon/BlackCatGuard', '运行状态：正在运行', '未找到数据库，已完成数据初始化创建。'):
            self.assertIn(value, text)
        self.assertNotIn('development', text)
        self.assertNotIn('unknown', text)

    async def test_existing_database_banner(self):
        with self.assertLogs('uvicorn.error', level='INFO') as output:
            await log_startup(database_existed=True)
        text = '\n'.join(output.output)
        self.assertIn('数据存储：已找到数据库，已完成数据加载。', text)
        self.assertNotIn('未找到数据库', text)


    async def test_container_address_uses_external_mapping(self):
        with patch.dict('os.environ', {'APP_ACCESS_URL': 'http://192.168.1.10:1888'}):
            with self.assertLogs('uvicorn.error', level='INFO') as output:
                await log_startup(database_existed=True)
        text = '\n'.join(output.output)
        self.assertIn('容器地址：http://192.168.1.10:1888', text)
        self.assertNotIn(':1777', text)


    async def test_local_start_always_displays_address(self):
        with patch.dict('os.environ', {}, clear=True), patch('sys.argv', ['main.py']):
            with self.assertLogs('uvicorn.error', level='INFO') as output:
                await log_startup(database_existed=True)
        self.assertIn('访问地址：http://127.0.0.1:1777', '\n'.join(output.output))

    def test_custom_cli_ports_and_ipv6(self):
        for args, expected in (
            (['uvicorn', 'main:app', '--host', '0.0.0.0', '--port', '1888'], 'http://127.0.0.1:1888'),
            (['uvicorn', 'main:app', '--host=::', '--port=1999'], 'http://[::1]:1999'),
            (['/venv/bin/uvicorn', 'main:app'], 'http://127.0.0.1:8000'),
        ):
            with patch.dict('os.environ', {}, clear=True), patch('sys.argv', args):
                self.assertEqual(local_access_url(), expected)
