from fastapi import APIRouter, Request
from fastapi.responses import RedirectResponse

from dao.models import Administrator
from utils.views import templates


router = APIRouter()


@router.get("/")
async def home(request: Request):
    if not await Administrator.exists():
        return RedirectResponse("/setup", status_code=303)

    admin = await Administrator.get_or_none(id=request.session.get("admin_id"))
    if admin is None:
        return RedirectResponse("/login", status_code=303)

    return templates.TemplateResponse(
        request=request, name="index.html",
        context={"page_title": "黑猫群管", "username": admin.username},
    )
