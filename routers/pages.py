from fastapi import APIRouter, Request
from fastapi.responses import RedirectResponse

from dao.models import Administrator
from utils.security import get_csrf_token
from utils.views import templates


router = APIRouter()


@router.get("/")
async def home(request: Request):
    # 没有管理员时，先打开初始化页面。
    if not await Administrator.exists():
        return RedirectResponse("/setup", status_code=303)

    # 从会话读取管理员 ID，并确认账号仍然存在。
    admin = await Administrator.get_or_none(id=request.session.get("admin_id"))
    if admin is None:
        # 未登录或原账号已删除，重新登录。
        return RedirectResponse("/login", status_code=303)

    # 身份验证通过后显示后台首页。
    return templates.TemplateResponse(
        request=request, name="index.html",
        context={
            "page_title": "黑猫群管",
            "username": admin.username,
            # 普通表单登录成功的提示只读取一次。
            "success": request.session.pop("success_message", ""),
            # 退出登录表单使用的校验令牌。
            "csrf_token": get_csrf_token(request),
        },
    )
