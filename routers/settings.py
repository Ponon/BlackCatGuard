"""系统设置：保存后对新发起的外部请求立即生效。"""
import re
from urllib.parse import urlsplit
from tortoise.exceptions import IntegrityError

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import RedirectResponse

from dao.models import Administrator, SystemSettings
from routers.group import require_admin, require_csrf
from utils.security import get_csrf_token, hash_password, verify_password
from utils.views import templates

router = APIRouter()


def validate_proxy(value: str) -> str:
    value = value.strip()
    if not value:
        return ""
    try:
        parsed = urlsplit(value)
        valid = (
            len(value) <= 2048
            and not any(char.isspace() or ord(char) < 32 for char in value)
            and parsed.scheme in ('http', 'https')
            and parsed.hostname
            and parsed.port is not None
            and 1 <= parsed.port <= 65535
            and parsed.path in ('', '/')
            and not parsed.query and not parsed.fragment
        )
    except ValueError:
        valid = False
    if not valid:
        raise HTTPException(status_code=400, detail="请输入完整的 HTTP/HTTPS 代理地址，例如 http://127.0.0.1:7897。")
    return value


@router.get('/settings')
async def settings_page(request: Request):
    if not await Administrator.exists():
        return RedirectResponse('/setup', status_code=303)
    admin = await Administrator.get_or_none(id=request.session.get('admin_id'))
    if admin is None:
        return RedirectResponse('/login', status_code=303)
    settings = await SystemSettings.get_or_none(id=1)
    return templates.TemplateResponse(
        request=request, name='settings.html',
        context={
            'page_title': '系统设置 · 黑猫群管', 'active_page': 'settings',
            'username': admin.username, 'csrf_token': get_csrf_token(request),
            'proxy_url': settings.proxy_url if settings else '',
        },
        headers={'Cache-Control': 'no-store'},
    )


@router.post('/settings')
async def save_settings(request: Request, proxy_url: str = Form(''), csrf_token: str = Form('')):
    await require_admin(request)
    require_csrf(request, csrf_token)
    proxy_url = validate_proxy(proxy_url)
    await SystemSettings.update_or_create(id=1, defaults={'proxy_url': proxy_url})
    return {'message': '系统设置已保存，新连接将使用代理。' if proxy_url else '系统设置已保存，已恢复默认网络。', 'proxy_url': proxy_url}


@router.post('/settings/account')
async def update_account(
    request: Request,
    username: str = Form(''),
    current_password: str = Form(''),
    new_password: str = Form(''),
    confirm_password: str = Form(''),
    csrf_token: str = Form(''),
):
    admin = await require_admin(request)
    require_csrf(request, csrf_token)
    # 修改账号前验证原密码；新密码留空仅修改用户名。
    if not verify_password(current_password, admin.password_hash):
        raise HTTPException(status_code=400, detail='当前密码不正确。')
    username = username.strip()
    if not re.fullmatch(r'[A-Za-z0-9_]{3,32}', username):
        raise HTTPException(status_code=400, detail='用户名需为 3～32 位字母、数字或下划线。')
    if new_password or confirm_password:
        if not 8 <= len(new_password) <= 128:
            raise HTTPException(status_code=400, detail='新密码需为 8～128 位。')
        if new_password != confirm_password:
            raise HTTPException(status_code=400, detail='两次输入的新密码不一致。')
    if username == admin.username and not new_password:
        raise HTTPException(status_code=400, detail='账号信息没有修改。')
    # 条件更新避免两个并发修改请求互相覆盖。
    values = {'username': username}
    if new_password:
        values['password_hash'] = hash_password(new_password)
    try:
        updated = await Administrator.filter(id=admin.id, password_hash=admin.password_hash).update(**values)
    except IntegrityError:
        raise HTTPException(status_code=400, detail='该用户名已被使用。') from None
    if not updated:
        raise HTTPException(status_code=409, detail='账号信息已经变更，请重新登录。')
    # 修改成功后退出当前登录状态，以新账号信息重新登录。
    request.session.clear()
    return {'message': '账号信息已更新，请重新登录。', 'redirect': '/login'}
