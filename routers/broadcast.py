"""群广播独立配置，复用群管理中的机器人和目标群。"""
from services.runtime_health import ensure_can_enable
import json
import re
from typing import Annotated
from urllib.parse import urlsplit

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import RedirectResponse
from tortoise.transactions import in_transaction

from dao.models import Administrator, BroadcastContent, BroadcastTask, BroadcastSchedule, GroupConfig
from routers.group import require_admin, require_csrf
from services.broadcast import schedule_description
from services.configuration import group_is_configured
from utils.security import get_csrf_token
from utils.views import templates

router = APIRouter()


def validate_buttons(raw):
    try:
        items = json.loads(raw)
    except (ValueError, TypeError):
        raise HTTPException(status_code=400, detail='链接按钮格式不正确。') from None
    if not isinstance(items, list) or len(items) > 24:
        raise HTTPException(status_code=400, detail='最多添加 24 个链接按钮。')
    buttons = []
    for item in items:
        if not isinstance(item, dict):
            raise HTTPException(status_code=400, detail='链接按钮格式不正确。')
        text, url, row = item.get('text'), item.get('url'), item.get('row')
        if not isinstance(text, str) or not isinstance(url, str) or type(row) is not int:
            raise HTTPException(status_code=400, detail='请填写按钮文字、链接和行号。')
        text, url = text.strip(), url.strip()
        if not text or len(text) > 64 or not 1 <= row <= 8:
            raise HTTPException(status_code=400, detail='按钮文字需为 1～64 个字符，行号为 1～8。')
        try:
            parsed = urlsplit(url)
            valid = parsed.scheme in ('http', 'https') and parsed.hostname and not parsed.username and not parsed.password
            parsed.port
        except ValueError:
            valid = False
        if not valid or len(url) > 2048 or any(char.isspace() or ord(char) < 32 for char in url):
            raise HTTPException(status_code=400, detail='按钮链接需为有效的 HTTP/HTTPS 地址。')
        if sum(button['row'] == row for button in buttons) >= 8:
            raise HTTPException(status_code=400, detail='同一行最多放置 8 个按钮。')
        buttons.append({'text': text, 'url': url, 'row': row})
    return buttons


def content_data(content):
    return {'id': content.id, 'name': content.name, 'message': content.message, 'buttons': content.buttons}


def task_data(task):
    return {
        'id': task.id, 'content_id': task.content_id,
        'enabled': task.enabled, 'schedule_mode': task.schedule_mode,
        'interval_minutes': task.interval_minutes, 'times': task.times,
        'description': schedule_description(task.schedule_mode, task.interval_minutes, task.times),
    }


@router.get('/broadcast')
async def broadcast_page(request: Request):
    if not await Administrator.exists():
        return RedirectResponse('/setup', status_code=303)
    admin = await Administrator.get_or_none(id=request.session.get('admin_id'))
    if admin is None:
        return RedirectResponse('/login', status_code=303)
    group = await GroupConfig.get_or_none(id=1)
    configured = bool(group and group.bot_token and group.chat_id)
    contents = await BroadcastContent.all().order_by('id')
    tasks = await BroadcastTask.all().order_by('id')
    task_values = [task_data(task) for task in tasks]
    for value in task_values:
        value['enabled'] = bool(configured and value['enabled'])
    return templates.TemplateResponse(request=request, name='broadcast.html', context={
        'page_title': '群广播 - 黑猫群管', 'active_page': 'broadcast',
        'username': admin.username, 'csrf_token': get_csrf_token(request),
        'group_configured': configured, 'contents': [content_data(content) for content in contents],
        'tasks': task_values,
        'target_group': group.chat_id if configured else '尚未配置目标群',
    }, headers={'Cache-Control': 'no-store'})


@router.post('/broadcast/content')
async def save_content(
    request: Request, content_id: Annotated[int, Form()] = 0,
    name: Annotated[str, Form()] = '', message: Annotated[str, Form()] = '',
    buttons: Annotated[str, Form()] = '[]', csrf_token: Annotated[str, Form()] = '',
):
    await require_admin(request)
    require_csrf(request, csrf_token)
    name = name.strip()
    if not name or len(name) > 64:
        raise HTTPException(status_code=400, detail='内容名称需为 1～64 个字符。')
    if not message.strip() or len(message.replace('{datetime}', '2000-01-01 00:00:00')) > 4096:
        raise HTTPException(status_code=400, detail='广播内容不能为空，替换时间变量后最多 4096 个字符。')
    if len(buttons) > 60000:
        raise HTTPException(status_code=400, detail='链接按钮内容过长。')
    values = validate_buttons(buttons)
    async with in_transaction():
        if content_id:
            content = await BroadcastContent.get_or_none(id=content_id)
            if content is None:
                raise HTTPException(status_code=404, detail='广播内容已删除，请刷新页面。')
            content.name, content.message, content.buttons = name, message, values
            await content.save()
        else:
            content = await BroadcastContent.create(name=name, message=message, buttons=values)
    return {'message': '广播内容已保存。', 'content': content_data(content)}


@router.post('/broadcast/content/delete')
async def delete_content(request: Request, content_id: Annotated[int, Form()], csrf_token: Annotated[str, Form()] = ''):
    await require_admin(request)
    require_csrf(request, csrf_token)
    async with in_transaction():
        if await BroadcastTask.filter(content_id=content_id).exists():
            raise HTTPException(status_code=409, detail='请先删除绑定任务或更换任务绑定的内容')
        if not await BroadcastContent.filter(id=content_id).delete():
            raise HTTPException(status_code=404, detail='广播内容已删除，请刷新页面。')
    return {'message': '广播内容已删除。'}


@router.post('/broadcast/task')
async def save_task(
    request: Request, task_id: Annotated[int, Form()] = 0,
    content_id: Annotated[int, Form()] = 0,
    schedule_mode: Annotated[str, Form()] = 'daily',
    interval_minutes: Annotated[str, Form()] = '60', times: Annotated[str, Form()] = '',
    csrf_token: Annotated[str, Form()] = '',
):
    await require_admin(request)
    require_csrf(request, csrf_token)
    if schedule_mode not in ('interval', 'daily'):
        raise HTTPException(status_code=400, detail='请选择有效的循环方式。')
    interval = 60
    if re.fullmatch(r'[0-9]{1,5}', interval_minutes) and 1 <= int(interval_minutes) <= 43200:
        interval = int(interval_minutes)
    elif schedule_mode == 'interval':
        raise HTTPException(status_code=400, detail='广播间隔需为 1～43200 分钟的整数。')
    if len(times) > 300:
        raise HTTPException(status_code=400, detail='每天最多设置 48 个时间点。')
    selected_times = sorted(set(value.strip() for value in times.split(',') if value.strip()))
    if schedule_mode != 'interval' and (not selected_times or len(selected_times) > 48 or any(
        not re.fullmatch(r'(?:[01][0-9]|2[0-3]):[0-5][0-9]', value) for value in selected_times
    )):
        raise HTTPException(status_code=400, detail='请设置 1 至 48 个有效发送时间')
    if schedule_mode == 'interval':
        selected_times = []
    async with in_transaction():
        content = await BroadcastContent.get_or_none(id=content_id)
        if content is None or not content.message.strip():
            raise HTTPException(status_code=400, detail='请先保存广播内容，再选择要绑定的内容。')
        values = {
            'content_id': content.id,
            'schedule_mode': schedule_mode, 'interval_minutes': interval,
            'times': selected_times,
        }
        if task_id:
            task = await BroadcastTask.get_or_none(id=task_id)
            if task is None:
                raise HTTPException(status_code=404, detail='循环任务已删除，请刷新页面。')
            rule_changed = any(getattr(task, field) != values[field] for field in ('schedule_mode', 'interval_minutes', 'times'))
            task.update_from_dict(values)
            await task.save(update_fields=list(values))
            if rule_changed:
                await BroadcastSchedule.filter(task_id=task.id).delete()
        else:
            task = await BroadcastTask.create(**values)
    return {'message': '循环广播已保存。', 'task': task_data(task)}


@router.post('/broadcast/task/delete')
async def delete_task(request: Request, task_id: Annotated[int, Form()], csrf_token: Annotated[str, Form()] = ''):
    await require_admin(request)
    require_csrf(request, csrf_token)
    if not await BroadcastTask.filter(id=task_id).delete():
        raise HTTPException(status_code=404, detail='循环任务已删除，请刷新页面。')
    return {'message': '循环任务已删除。'}


@router.post('/broadcast/task/toggle')
async def toggle_task(
    request: Request, task_id: Annotated[int, Form()],
    enabled: Annotated[str, Form()] = 'false', csrf_token: Annotated[str, Form()] = '',
):
    await require_admin(request)
    require_csrf(request, csrf_token)
    if enabled not in ('true', 'false'):
        raise HTTPException(status_code=400, detail='启用状态不正确。')
    async with in_transaction():
        task = await BroadcastTask.filter(id=task_id).select_for_update().first()
        if task is None:
            raise HTTPException(status_code=404, detail='循环任务已删除，请刷新页面。')
        if enabled == 'true':
            await ensure_can_enable()
            if not await group_is_configured(lock=True):
                raise HTTPException(status_code=409, detail='请先完成群管理配置')
            content = await BroadcastContent.get_or_none(id=task.content_id)
            if content is None or not content.message.strip():
                raise HTTPException(status_code=400, detail='广播内容不可用，请先编辑保存内容。')
        if task.enabled != (enabled == 'true'):
            await BroadcastSchedule.filter(task_id=task.id).delete()
        task.enabled = enabled == 'true'
        await task.save(update_fields=['enabled'])
    return {'message': '循环广播已开启' if task.enabled else '循环广播已关闭。', 'task': task_data(task)}
