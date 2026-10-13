"""单群配置与 Telegram 连接检测。"""
import re
import secrets
from tortoise.transactions import in_transaction

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import RedirectResponse
from starlette.concurrency import run_in_threadpool

from dao.models import Administrator, GroupConfig, VerificationSettings, WelcomeSettings, KeywordSettings, AISettings, BroadcastSettings, BroadcastTask, BroadcastSchedule, JoinSession
from services.telegram import inspect_group
from services.network import get_proxy_url
from services.runtime_health import probe_lock, record_probe
from utils.security import get_csrf_token, valid_csrf_token
from utils.views import templates

router = APIRouter()


async def require_admin(request: Request):
    admin = await Administrator.get_or_none(id=request.session.get("admin_id"))
    if admin is None:
        raise HTTPException(status_code=401, detail="登录已过期，请重新登录。")
    return admin


def require_csrf(request: Request, token: str):
    if not valid_csrf_token(request, token):
        raise HTTPException(status_code=403, detail="表单已过期，请刷新页面后重试。")


@router.get("/group")
async def group_page(request: Request):
    if not await Administrator.exists():
        return RedirectResponse("/setup", status_code=303)
    admin = await Administrator.get_or_none(id=request.session.get("admin_id"))
    if admin is None:
        return RedirectResponse("/login", status_code=303)
    config = await GroupConfig.get_or_none(id=1)
    # 仅登录管理员可读取 Token，页面默认使用密码框遮蔽并禁止缓存。
    return templates.TemplateResponse(
        request=request, name="group.html",
        context={
            "page_title": "群管理 · 黑猫群管",
            "username": admin.username,
            "active_page": "group",
            "csrf_token": get_csrf_token(request),
            "configured": bool(config and config.bot_token and config.chat_id),
            "bot_token": config.bot_token if config else "",
            "chat_id": config.chat_id if config else "",
        },
        headers={"Cache-Control": "no-store"},
    )


@router.post("/group/config")
async def save_config(request: Request, chat_id: str = Form(""), bot_token: str = Form(""), csrf_token: str = Form("")):
    await require_admin(request)
    require_csrf(request, csrf_token)
    token = bot_token.strip()
    chat_id = chat_id.strip()
    # 两项均为空表示明确清除配置，单项缺失仍提示用户补全。
    if not token and not chat_id:
        # 删除配置与关闭两个关联功能必须一起提交。
        async with in_transaction():
            await GroupConfig.filter(id=1).delete()
            await VerificationSettings.filter(id=1).update(enabled=False)
            await WelcomeSettings.filter(id=1).update(enabled=False)
            await KeywordSettings.filter(id=1).update(enabled=False)
            await AISettings.filter(id=1).update(enabled=False)
            await BroadcastSettings.filter(id=1).update(enabled=False)
            await BroadcastTask.all().update(enabled=False)
            await BroadcastSchedule.all().delete()
            await JoinSession.all().exclude(state='done').update(state='done')
        return {"message": "群配置已清除并关闭关联功能", "configured": False, "chat_id": "", "bot_token": ""}
    if not re.fullmatch(r"[0-9]{1,20}:[A-Za-z0-9_-]{20,200}", token):
        raise HTTPException(status_code=400, detail="请输入有效的 Bot Token。")
    if not re.fullmatch(r"-[1-9][0-9]{0,19}", chat_id):
        raise HTTPException(status_code=400, detail="群 ID 必须是负整数")
    async with in_transaction():
        old = await GroupConfig.filter(id=1).select_for_update().first()
        if old and (old.bot_token != token or old.chat_id != chat_id):
            for model in (VerificationSettings, WelcomeSettings, KeywordSettings, AISettings, BroadcastSettings):
                await model.filter(id=1).update(enabled=False)
            await BroadcastTask.all().update(enabled=False)
            await BroadcastSchedule.all().delete()
            await JoinSession.all().exclude(state='done').update(state='done')
        await GroupConfig.update_or_create(id=1, defaults={
            "bot_token": token, "chat_id": chat_id,
            "revision": old.revision if old and old.bot_token == token and old.chat_id == chat_id else secrets.token_hex(16), "check_result": {},
        })
    # 保存接口直接执行一次检测，调用方不需要额外发起检测请求。
    result = await check_config(request, csrf_token)
    return {"message": "配置保存成功并检测通过" if result.get("ok") else "配置保存成功但检测未通过请检查网络设置和机器人权限后重新检测", "configured": True,
            "chat_id": chat_id, "bot_token": token, "check_result": result}


@router.post("/group/check")
async def check_config(request: Request, csrf_token: str = Form("")):
    await require_admin(request)
    require_csrf(request, csrf_token)
    async with probe_lock:
        config = await GroupConfig.get_or_none(id=1)
        if config is None:
            raise HTTPException(status_code=400, detail="请先保存机器人和群组配置")
        proxy_url = await get_proxy_url()
        result = await run_in_threadpool(inspect_group, config.bot_token, config.chat_id, proxy_url)
        # 代理也属于检测快照，拒绝把切换代理前的结果写回新配置。
        if await get_proxy_url() != proxy_url:
            raise HTTPException(status_code=409, detail="网络设置已经变更请重新检测")
        result = await record_probe(config, proxy_url, result)
        if result is None:
            raise HTTPException(status_code=409, detail="配置已经变更请重新检测")
        updated = await GroupConfig.filter(id=1, revision=config.revision).update(check_result=result)
        if not updated:
            raise HTTPException(status_code=409, detail="配置已经变更请重新检测")
        return result
