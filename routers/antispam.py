"""广告拦截配置和只读消息测试，不执行群内删除。"""
from typing import Annotated
from types import SimpleNamespace
import re
from urllib.parse import urlsplit

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import RedirectResponse
from starlette.concurrency import run_in_threadpool
from tortoise.transactions import in_transaction

from dao.models import Administrator, GroupConfig, KeywordSettings, AISettings, WhitelistSettings, EventLog
from routers.group import require_admin, require_csrf
from services.antispam import classify_message
from services.network import get_proxy_url
from utils.security import get_csrf_token
from utils.views import templates

router = APIRouter()


async def require_group(enabled):
    if enabled not in ('true', 'false'):
        raise HTTPException(status_code=400, detail='启用状态不正确。')
    if enabled == 'true' and not await GroupConfig.filter(id=1).select_for_update().exists():
        raise HTTPException(status_code=409, detail='请先在群管理中配置机器人 Token 和群 ID。')


@router.get('/antispam')
async def antispam_page(request: Request):
    if not await Administrator.exists():
        return RedirectResponse('/setup', status_code=303)
    admin = await Administrator.get_or_none(id=request.session.get('admin_id'))
    if admin is None:
        return RedirectResponse('/login', status_code=303)
    keyword = await KeywordSettings.get_or_none(id=1)
    ai = await AISettings.get_or_none(id=1)
    whitelist = await WhitelistSettings.get_or_none(id=1)
    configured = await GroupConfig.filter(id=1).exists()
    return templates.TemplateResponse(request=request, name='antispam.html', context={
        'page_title': '广告拦截 · 黑猫群管', 'active_page': 'antispam',
        'username': admin.username, 'csrf_token': get_csrf_token(request), 'group_configured': configured,
        'keyword_enabled': bool(configured and keyword and keyword.enabled),
        'keywords': '\n'.join(keyword.keywords) if keyword else '',
        'ai_enabled': bool(configured and ai and ai.enabled),
        'base_url': ai.base_url if ai else '', 'model': ai.model if ai else '',
        'has_api_key': bool(ai and ai.api_key),
        'whitelist': '\n'.join(whitelist.entries) if whitelist else '',
    }, headers={'Cache-Control': 'no-store'})


@router.post('/antispam/keywords')
async def save_keywords(request: Request, enabled: Annotated[str, Form()] = 'false', keywords: Annotated[str, Form()] = '', csrf_token: Annotated[str, Form()] = ''):
    await require_admin(request)
    require_csrf(request, csrf_token)
    if len(keywords) > 20000:
        raise HTTPException(status_code=400, detail='关键词内容最多 20000 个字符。')
    words, seen = [], set()
    for line in keywords.splitlines():
        word = line.strip()
        if word and word.casefold() not in seen:
            words.append(word)
            seen.add(word.casefold())
    if len(words) > 500 or any(len(word) > 100 for word in words):
        raise HTTPException(status_code=400, detail='最多 500 个关键词，每个最多 100 个字符。')
    if enabled == 'true' and not words:
        raise HTTPException(status_code=400, detail='启用关键词拦截前请至少填写一个关键词。')
    async with in_transaction():
        await require_group(enabled)
        await KeywordSettings.update_or_create(id=1, defaults={'enabled': enabled == 'true', 'keywords': words})
    return {'message': '关键词规则已保存。', 'keywords': '\n'.join(words)}


@router.post('/antispam/ai')
async def save_ai(request: Request, enabled: Annotated[str, Form()] = 'false', base_url: Annotated[str, Form()] = '', model: Annotated[str, Form()] = '', api_key: Annotated[str, Form()] = '', clear_api_key: Annotated[str, Form()] = 'false', csrf_token: Annotated[str, Form()] = ''):
    await require_admin(request)
    require_csrf(request, csrf_token)
    base_url, model, api_key = base_url.strip().rstrip('/'), model.strip(), api_key.strip()
    if clear_api_key not in ('true', 'false'):
        raise HTTPException(status_code=400, detail='密钥清除状态不正确。')
    if base_url:
        try:
            parsed = urlsplit(base_url)
            valid = parsed.scheme in ('http', 'https') and parsed.hostname and not parsed.username and not parsed.password and not parsed.query and not parsed.fragment
            parsed.port
        except ValueError:
            valid = False
        if not valid or len(base_url) > 2048 or any(c.isspace() for c in base_url):
            raise HTTPException(status_code=400, detail='请输入有效的接口基础地址，如 https://服务地址/v1。')
        if base_url.endswith('/chat/completions'):
            base_url = base_url[:-len('/chat/completions')]
    if len(model) > 128 or len(api_key) > 4096 or any(ord(c) < 32 for c in model + api_key):
        raise HTTPException(status_code=400, detail='模型名称或 API Key 格式不正确。')
    if enabled == 'true' and (not base_url or not model):
        raise HTTPException(status_code=400, detail='启用 AI 前请填写接口基础地址和模型名称。')
    async with in_transaction():
        await require_group(enabled)
        old = await AISettings.get_or_none(id=1)
        key = '' if clear_api_key == 'true' else (api_key or (old.api_key if old else ''))
        await AISettings.update_or_create(id=1, defaults={'enabled': enabled == 'true', 'base_url': base_url, 'model': model, 'api_key': key})
    return {'message': 'AI 识别配置已保存。', 'has_api_key': bool(key), 'base_url': base_url}


@router.post('/antispam/test')
async def test_message(request: Request, text: Annotated[str, Form()] = '', csrf_token: Annotated[str, Form()] = '', user_id: Annotated[str, Form()] = '', username: Annotated[str, Form()] = ''):
    await require_admin(request)
    require_csrf(request, csrf_token)
    user_id, username = user_id.strip(), username.strip().lstrip('@')
    if (user_id and (not user_id.isascii() or not user_id.isdigit() or len(user_id) > 20)) or len(username) > 64:
        raise HTTPException(status_code=400, detail='测试用户 ID 或用户名格式不正确。')
    if not text.strip() or len(text) > 10000:
        raise HTTPException(status_code=400, detail='请输入 1～10000 个字符的测试消息。')
    keyword = await KeywordSettings.get_or_none(id=1)
    ai = await AISettings.get_or_none(id=1)
    # 测试仅根据配置是否完整选择规则，不修改数据库中的正式启用状态。
    keyword = SimpleNamespace(enabled=True, keywords=keyword.keywords) if keyword and keyword.keywords else None
    ai = SimpleNamespace(enabled=True, base_url=ai.base_url, model=ai.model, api_key=ai.api_key) if ai and ai.base_url and ai.model else None
    whitelist = await WhitelistSettings.get_or_none(id=1)
    entries = whitelist.entries if whitelist else []
    if keyword is None and ai is None and not entries:
        raise HTTPException(status_code=400, detail='请先保存关键词，或保存完整的 AI 接口地址和模型名称，无需启用规则。')
    result = await run_in_threadpool(classify_message, text, keyword, ai, await get_proxy_url(), entries, user_id, username)
    await EventLog.create(event_type='消息测试', content=text, username='@' + username if username else '', result=result['message'][:500])
    return result


@router.post('/antispam/whitelist')
async def save_whitelist(request: Request, entries: Annotated[str, Form()] = '', csrf_token: Annotated[str, Form()] = ''):
    await require_admin(request)
    require_csrf(request, csrf_token)
    if len(entries) > 20000:
        raise HTTPException(status_code=400, detail='白名单内容最多 20000 个字符。')
    values = []
    seen = set()
    for line in entries.splitlines():
        value = line.strip()
        if not value:
            continue
        if not re.fullmatch(r'[1-9][0-9]{0,19}|@[A-Za-z0-9_]{1,64}', value):
            raise HTTPException(status_code=400, detail='白名单每行填写一个用户数字 ID 或 @用户名。')
        if value.casefold() not in seen:
            values.append(value)
            seen.add(value.casefold())
    if len(values) > 500:
        raise HTTPException(status_code=400, detail='白名单最多 500 个用户。')
    await WhitelistSettings.update_or_create(id=1, defaults={'entries': values})
    return {'message': '白名单已保存。', 'entries': '\n'.join(values)}
