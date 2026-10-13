"""日志链：同一操作共用 flow_id，子事件明确引用原因事件。"""
from dao.models import EventLog


async def record_event(*, flow_id='', parent_event_id=None, **values):
    if flow_id:
        if parent_event_id is None:
            previous = await EventLog.filter(flow_id=flow_id).exclude(event_type='系统推送').order_by('-id').first()
            parent_event_id = previous.id if previous else None
        values['flow_id'] = flow_id
    return await EventLog.create(parent_event_id=parent_event_id, **values)
