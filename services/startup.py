"""启动横幅 仅输出项目和数据库状态"""
import logging
import os
import sys
import ipaddress

logger = logging.getLogger('uvicorn.error')

def local_access_url():
    host = os.environ.get('UVICORN_HOST', '127.0.0.1')
    executable = sys.argv[0].replace('\\', '/')
    default_port = '8000' if '/uvicorn/' in executable or executable.endswith('/uvicorn') else '1777'
    port = os.environ.get('UVICORN_PORT', default_port)
    # 支持 uvicorn --port 1888 和 --port=1888 两种启动方式
    for index, argument in enumerate(sys.argv[1:], 1):
        for option in ('host', 'port'):
            value = None
            if argument == '--' + option and index + 1 < len(sys.argv):
                value = sys.argv[index + 1]
            elif argument.startswith('--' + option + '='):
                value = argument.split('=', 1)[1]
            if value is not None:
                if option == 'host':
                    host = value
                else:
                    port = value
    if host in ('0.0.0.0', '::'):
        host = '127.0.0.1' if host == '0.0.0.0' else '::1'
    try:
        if ipaddress.ip_address(host).version == 6:
            host = f'[{host}]'
    except ValueError:
        pass
    return f'http://{host}:{port}'


async def log_startup(*, database_existed):
    storage = ('已找到数据库，已完成数据加载。' if database_existed
               else '未找到数据库，已完成数据初始化创建。')
    address = os.environ.get('APP_ACCESS_URL', '').strip()
    address_line = f'容器地址：{address}\n' if address else f'访问地址：{local_access_url()}\n'
    logger.info(
        '项目名称：黑猫群管\n'
        '项目作者：Ponon\n'
        '项目地址：https://github.com/Ponon/BlackCatGuard\n'
        '运行状态：正在运行\n'
        '%s'
        '数据存储：%s\n',
        address_line, storage,
    )
