"""统一外部 HTTP 请求入口；显式代理不受系统 NO_PROXY 绕过规则影响。"""
import base64
import ssl

import certifi
from urllib.parse import unquote, urlsplit
from urllib.request import HTTPSHandler, ProxyHandler, build_opener

from dao.models import SystemSettings


async def get_proxy_url() -> str:
    settings = await SystemSettings.get_or_none(id=1)
    return settings.proxy_url if settings else ""


class ExplicitProxyHandler(ProxyHandler):
    def proxy_open(self, request, proxy, protocol):
        # 用户明确配置代理时，每个 HTTP/HTTPS 请求均使用该代理。
        parsed = urlsplit(proxy)
        host = parsed.hostname
        if ':' in host:
            host = f'[{host}]'
        if parsed.port:
            host += f':{parsed.port}'
        if parsed.username is not None:
            credentials = f'{unquote(parsed.username)}:{unquote(parsed.password or "")}'
            encoded = base64.b64encode(credentials.encode()).decode('ascii')
            request.add_header('Proxy-Authorization', f'Basic {encoded}')
        original = request.type
        request.set_proxy(host, parsed.scheme)
        if original == parsed.scheme or original == 'https':
            return None
        return self.parent.open(request, timeout=request.timeout)


def open_request(request, *, proxy_url: str = "", timeout: int = 10, direct: bool = False):
    # 每次新建 opener，避免保存配置后继续使用旧代理或污染进程全局状态。
    handler = ProxyHandler({}) if direct else (ExplicitProxyHandler({'http': proxy_url, 'https': proxy_url}) if proxy_url else ProxyHandler())
    # 显式加载可信根证书，避免 Python 安装缺少系统 CA 时连接失败。
    context = ssl.create_default_context(cafile=certifi.where())
    return build_opener(handler, HTTPSHandler(context=context)).open(request, timeout=timeout)
