"""系统设置：保存后对新发起的外部请求立即生效。"""
import re
import secrets
from typing import Annotated
from urllib.parse import urlsplit
from tortoise.exceptions import IntegrityError

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import RedirectResponse

from dao.models import Administrator, SystemSettings, PushSettings
from starlette.concurrency import run_in_threadpool
from services.notifications import EVENT_OPTIONS, send_bark, PushError
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
        raise HTTPException(status_code=400, detail="请输入完整有效的代理地址")
    return value


@router.get('/settings')
async def settings_page(request: Request):
    if not await Administrator.exists():
        return RedirectResponse('/setup', status_code=303)
    admin = await Administrator.get_or_none(id=request.session.get('admin_id'))
    if admin is None:
        return RedirectResponse('/login', status_code=303)
    settings = await SystemSettings.get_or_none(id=1)
    push = await PushSettings.get_or_none(id=1)
    return templates.TemplateResponse(
        request=request, name='settings.html',
        context={
            'page_title': '系统设置 · 黑猫群管', 'active_page': 'settings',
            'username': admin.username, 'csrf_token': get_csrf_token(request),
            'proxy_url': settings.proxy_url if settings else '',
            'network_saved': settings is not None,
            'log_retention_days': settings.log_retention_days if settings else 30,
            'push_saved': push is not None, 'push_provider': push.provider if push else '',
            'bark_url': push.bark_url if push else '', 'push_events': push.events if push else [],
            'push_event_options': EVENT_OPTIONS,
        },
        headers={'Cache-Control': 'no-store'},
    )


@router.post('/settings')
async def save_settings(request: Request, proxy_url: str = Form(''), csrf_token: str = Form('')):
    await require_admin(request)
    require_csrf(request, csrf_token)
    proxy_url = validate_proxy(proxy_url)
    await SystemSettings.update_or_create(id=1, defaults={'proxy_url': proxy_url})
    return {'message': '代理设置已保存' if proxy_url else '已恢复默认网络', 'proxy_url': proxy_url}


def validate_bark_url(value):
    value = value.strip().rstrip('/')
    if not value:
        return ''
    try:
        parsed = urlsplit(value)
        valid = parsed.scheme in ('http', 'https') and parsed.hostname and parsed.path.strip('/') and not (
            parsed.username or parsed.password or parsed.query or parsed.fragment
        ) and len(value) <= 2048 and not any(char.isspace() or ord(char) < 32 for char in value)
        parsed.port
    except ValueError:
        valid = False
    if not valid:
        raise HTTPException(status_code=400, detail='请输入包含设备 Key 的完整 Bark 推送地址')
    return value


@router.post('/settings/push')
async def save_push_settings(request: Request, provider: Annotated[str, Form()] = '',
    bark_url: Annotated[str, Form()] = '', events: Annotated[list[str], Form()] = [], csrf_token: Annotated[str, Form()] = ''):
    await require_admin(request)
    require_csrf(request, csrf_token)
    if provider not in ('', 'bark'):
        raise HTTPException(status_code=400, detail='请选择有效的推送方式')
    url = validate_bark_url(bark_url)
    if any(value not in EVENT_OPTIONS for value in events):
        raise HTTPException(status_code=400, detail='请选择有效的推送事件')
    selected = list(dict.fromkeys(events))
    if provider == 'bark' and (not url or not selected):
        raise HTTPException(status_code=400, detail='请填写 Bark 地址并至少选择一个推送事件')
    await PushSettings.update_or_create(id=1, defaults={
        'provider': provider, 'bark_url': url, 'events': selected, 'revision': secrets.token_hex(16),
    })
    return {'message': '系统推送设置已保存', 'provider': provider, 'bark_url': url, 'events': selected}


@router.post('/settings/push/test')
async def test_push(request: Request, csrf_token: Annotated[str, Form()] = ''):
    await require_admin(request)
    require_csrf(request, csrf_token)
    settings = await PushSettings.get_or_none(id=1)
    if not settings or not settings.bark_url:
        raise HTTPException(status_code=400, detail='请先保存 Bark 推送地址')
    try:
        await run_in_threadpool(send_bark, settings.bark_url, '黑猫群管', '系统推送连接测试')
    except PushError as error:
        raise HTTPException(status_code=502, detail=str(error)) from None
    return {'message': 'Bark 推送服务已接收测试消息'}


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


@router.post('/settings/logs')
async def save_log_settings(request: Request, log_retention_days: Annotated[str, Form()] = '', csrf_token: Annotated[str, Form()] = ''):
    await require_admin(request)
    require_csrf(request, csrf_token)
    value = log_retention_days.strip()
    if not re.fullmatch(r'[0-9]{1,6}', value) or int(value) > 36500:
        raise HTTPException(status_code=400, detail='日志保留天数需为 0 到 36500 的整数')
    days = int(value)
    await SystemSettings.update_or_create(id=1, defaults={'log_retention_days': days})
    return {'message': '日志保留设置已保存', 'log_retention_days': days}
