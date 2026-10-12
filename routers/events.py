"""事件日志按最新时间分页，仅读取真实保存的事件。"""
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Request, Form, HTTPException
from fastapi.responses import RedirectResponse

from dao.models import Administrator, EventLog
from routers.group import require_admin, require_csrf
from utils.security import get_csrf_token
from utils.views import templates

router = APIRouter()
PAGE_SIZES = (10, 20, 50, 100)


@router.get('/events')
async def events_page(request: Request, page: int = 1, page_size: int = 10):
    if not await Administrator.exists():
        return RedirectResponse('/setup', status_code=303)
    admin = await Administrator.get_or_none(id=request.session.get('admin_id'))
    if admin is None:
        return RedirectResponse('/login', status_code=303)
    # 限制每页条数，越界页自动回到最后一页。
    page_size = page_size if page_size in PAGE_SIZES else 10
    total = await EventLog.all().count()
    pages = max(1, (total + page_size - 1) // page_size)
    page = min(max(1, page), pages)
    records = await EventLog.all().order_by('-created_at', '-id').offset((page - 1) * page_size).limit(page_size)
    rows = []
    for record in records:
        timestamp = record.created_at
        if timestamp.tzinfo is None:
            timestamp = timestamp.replace(tzinfo=ZoneInfo('Asia/Shanghai'))
        rows.append({
            'time': timestamp.astimezone(ZoneInfo('Asia/Shanghai')).strftime('%Y-%m-%d %H:%M:%S'),
            'type': record.event_type, 'nickname': record.nickname,
            'username': record.username, 'content': record.content, 'result': record.result,
        })
    return templates.TemplateResponse(request=request, name='events.html', context={
        'page_title': '事件日志 · 黑猫群管', 'active_page': 'events',
        'username': admin.username, 'csrf_token': get_csrf_token(request),
        'rows': rows, 'total': total, 'page': page, 'pages': pages,
        'page_size': page_size, 'page_sizes': PAGE_SIZES,
        'success': request.session.pop('events_success', ''),
    }, headers={'Cache-Control': 'no-store'})


@router.post('/events/clear')
async def clear_events(request: Request, csrf_token: str = Form(''), confirmed: str = Form('')):
    await require_admin(request)
    require_csrf(request, csrf_token)
    if confirmed != 'true':
        raise HTTPException(status_code=400, detail='请先确认清空所有日志。')
    # 直接删除数据库记录，不生成清空操作日志。
    await EventLog.all().delete()
    request.session['events_success'] = '所有日志已清空。'
    return {'message': '所有日志已清空。'}
