"""入群验证与欢迎语独立配置，后台事件服务读取最新设置。"""
from services.runtime_health import ensure_can_enable
from typing import Annotated
from tortoise.transactions import in_transaction

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import RedirectResponse

from dao.models import Administrator, VerificationSettings, WelcomeSettings
from routers.group import require_admin, require_csrf
from utils.security import get_csrf_token
from utils.views import templates
from services.configuration import group_is_configured, verification_is_enabled, welcome_is_enabled

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
    configured = await group_is_configured()
    return templates.TemplateResponse(
        request=request, name='verification.html',
        context={
            'page_title': '入群设置 · 黑猫群管', 'active_page': 'verification',
            'username': admin.username, 'csrf_token': get_csrf_token(request),
            'enabled': verification_is_enabled(settings, configured),
            'group_configured': configured,
            'verification_saved': settings is not None,
            'welcome_saved': welcome is not None,
            'method': settings.method if settings else 'button',
            'welcome_message': welcome.message if welcome else '欢迎 {nickname}（{username}）加入群组！',
            'welcome_enabled': welcome_is_enabled(welcome, configured),
            'timeout_action': settings.timeout_action if settings else 'kick',
            'wrong_action': settings.wrong_action if settings else 'none',
            'timeout_seconds': settings.timeout_seconds if settings else 60,
            'delete_after_seconds': welcome.delete_after_seconds if welcome else 60,
        },
        headers={'Cache-Control': 'no-store'},
    )


@router.post('/verification')
async def save_verification(
    request: Request, enabled: str = Form('false'), method: str = Form('button'), csrf_token: str = Form(''),
    timeout_seconds: Annotated[str, Form()] = '60',
    timeout_action: Annotated[str, Form()] = 'kick',
    wrong_action: Annotated[str, Form()] = 'none',
):
    await require_admin(request)
    require_csrf(request, csrf_token)
    if enabled not in ('true', 'false'):
        raise HTTPException(status_code=400, detail='启用状态不正确。')
    if method not in ('button', 'arithmetic'):
        raise HTTPException(status_code=400, detail='请选择按钮验证或算术验证。')
    if timeout_action not in ('restrict', 'kick') or wrong_action not in ('none', 'restrict', 'kick'):
        raise HTTPException(status_code=400, detail='请选择有效的验证失败处理方式')
    # 按字符串检查，避免小数、负数或空值被自动转换。
    if not timeout_seconds.isascii() or not timeout_seconds.isdigit() or not 10 <= int(timeout_seconds) <= 3600:
        raise HTTPException(status_code=400, detail='验证超时需为 10～3600 秒的整数。')
    async with in_transaction():
        if enabled == 'true' and not await group_is_configured(lock=True):
            raise HTTPException(status_code=409, detail='请先完成群管理配置')
        if enabled == 'true':
            await ensure_can_enable()
        await VerificationSettings.update_or_create(id=1, defaults={
            'enabled': enabled == 'true', 'method': method,
            'timeout_seconds': int(timeout_seconds), 'timeout_action': timeout_action, 'wrong_action': wrong_action,
        })
    return {'message': '入群验证设置已保存。', 'enabled': enabled == 'true', 'method': method, 'timeout_seconds': int(timeout_seconds), 'timeout_action': timeout_action, 'wrong_action': wrong_action}


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
        raise HTTPException(status_code=400, detail='欢迎语删除延时需为 0 至 86400 秒的整数')
    async with in_transaction():
        if enabled == 'true' and not await group_is_configured(lock=True):
            raise HTTPException(status_code=409, detail='请先完成群管理配置')
        if enabled == 'true':
            await ensure_can_enable()
        await WelcomeSettings.update_or_create(id=1, defaults={
            'enabled': enabled == 'true', 'message': welcome_message,
            'delete_after_seconds': int(delete_after_seconds),
        })
    return {'message': '入群欢迎语设置已保存。', 'enabled': enabled == 'true', 'welcome_message': welcome_message, 'delete_after_seconds': int(delete_after_seconds)}
