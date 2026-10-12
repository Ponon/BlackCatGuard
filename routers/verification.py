"""入群验证配置；实际入群事件处理服务独立接入。"""
from typing import Annotated
from tortoise.transactions import in_transaction

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import RedirectResponse

from dao.models import Administrator, VerificationSettings, WelcomeSettings, GroupConfig
from routers.group import require_admin, require_csrf
from utils.security import get_csrf_token
from utils.views import templates

router = APIRouter()


@router.get('/verification')
async def verification_page(request: Request):
    if not await Administrator.exists():
        return RedirectResponse('/setup', status_code=303)
    admin = await Administrator.get_or_none(id=request.session.get('admin_id'))
    if admin is None:
        return RedirectResponse('/login', status_code=303)
    settings = await VerificationSettings.get_or_none(id=1)
    welcome = await WelcomeSettings.get_or_none(id=1)
    configured = await GroupConfig.filter(id=1).exists()
    return templates.TemplateResponse(
        request=request, name='verification.html',
        context={
            'page_title': '入群设置 · 黑猫群管', 'active_page': 'verification',
            'username': admin.username, 'csrf_token': get_csrf_token(request),
            'enabled': bool(configured and settings and settings.enabled),
            'group_configured': configured,
            'method': settings.method if settings else 'button',
            'welcome_message': welcome.message if welcome else '欢迎 {nickname}（{username}）加入群组！',
            'welcome_enabled': bool(configured and welcome and welcome.enabled),
            'timeout_seconds': settings.timeout_seconds if settings else 60,
            'delete_after_seconds': welcome.delete_after_seconds if welcome else 60,
        },
        headers={'Cache-Control': 'no-store'},
    )


@router.post('/verification')
async def save_verification(
    request: Request, enabled: str = Form('false'), method: str = Form('button'), csrf_token: str = Form(''),
    timeout_seconds: Annotated[str, Form()] = '60',
):
    await require_admin(request)
    require_csrf(request, csrf_token)
    if enabled not in ('true', 'false'):
        raise HTTPException(status_code=400, detail='启用状态不正确。')
    if method not in ('button', 'arithmetic'):
        raise HTTPException(status_code=400, detail='请选择按钮验证或算术验证。')
    # 按字符串检查，避免小数、负数或空值被自动转换。
    if not timeout_seconds.isascii() or not timeout_seconds.isdigit() or not 10 <= int(timeout_seconds) <= 3600:
        raise HTTPException(status_code=400, detail='验证超时需为 10～3600 秒的整数。')
    async with in_transaction():
        if enabled == 'true' and not await GroupConfig.filter(id=1).select_for_update().exists():
            raise HTTPException(status_code=409, detail='请先在群管理中配置机器人 Token 和群 ID。')
        await VerificationSettings.update_or_create(id=1, defaults={
            'enabled': enabled == 'true', 'method': method,
            'timeout_seconds': int(timeout_seconds),
        })
    return {'message': '入群验证设置已保存。', 'enabled': enabled == 'true', 'method': method}


@router.post('/welcome')
async def save_welcome(
    request: Request,
    enabled: Annotated[str, Form()] = 'false',
    welcome_message: Annotated[str, Form()] = '',
    delete_after_seconds: Annotated[str, Form()] = '60',
    csrf_token: Annotated[str, Form()] = '',
):
    await require_admin(request)
    require_csrf(request, csrf_token)
    if enabled not in ('true', 'false'):
        raise HTTPException(status_code=400, detail='启用状态不正确。')
    if not welcome_message.strip() or len(welcome_message) > 2000:
        raise HTTPException(status_code=400, detail='欢迎语不能为空，最多 2000 个字符。')
    if not delete_after_seconds.isascii() or not delete_after_seconds.isdigit() or not 0 <= int(delete_after_seconds) <= 86400:
        raise HTTPException(status_code=400, detail='欢迎语删除延时需为 0～86400 秒的整数，0 表示不自动删除。')
    async with in_transaction():
        if enabled == 'true' and not await GroupConfig.filter(id=1).select_for_update().exists():
            raise HTTPException(status_code=409, detail='请先在群管理中配置机器人 Token 和群 ID。')
        await WelcomeSettings.update_or_create(id=1, defaults={
            'enabled': enabled == 'true', 'message': welcome_message,
            'delete_after_seconds': int(delete_after_seconds),
        })
    return {'message': '入群欢迎语设置已保存。'}
