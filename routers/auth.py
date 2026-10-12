import re
from tortoise.exceptions import IntegrityError
from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from database import create_admin, has_admin
from security import (
    get_csrf_token,
    hash_password,
    valid_csrf_token,
)
from views import templates

router = APIRouter()


# 打开初始化页面
@router.get("/setup", response_class=HTMLResponse)
async def setup_page(request: Request):
    # 已有管理员，跳转到登录页面。
    if await has_admin():
        return RedirectResponse("/login", status_code=303)

    # 没有管理员，显示创建管理员页面。
    return templates.TemplateResponse(
        request=request,
        name="setup.html",
        context={
            "page_title": "初始化 · 山雀群管",
            "csrf_token": get_csrf_token(request),
            "error": "",
            "username": "",
        },
    )


# 接收创建管理员的表单
@router.post("/setup", response_class=HTMLResponse)
async def setup_submit(
        request: Request,
        # 读取 HTML 表单中对应 name 的字段。
        username: str = Form(...),
        password: str = Form(...),
        confirm_password: str = Form(...),
        csrf_token: str = Form(...),
):
    # 防止重复创建管理员。
    if await has_admin():
        return RedirectResponse("/login", status_code=303)

    # 验证表单校验码。
    if not valid_csrf_token(request, csrf_token):
        raise HTTPException(
            status_code=403,
            detail="表单已过期，请刷新页面后重试。",
        )

    # 去掉账号前后的空格，密码保持原样。
    username = username.strip()

    error = ""

    # 按顺序检查账号、密码长度和确认密码。
    if not re.fullmatch(r"[A-Za-z0-9_]{3,32}", username):
        error = "账号需为 3～32 位字母、数字或下划线。"
    elif len(password) < 8:
        error = "密码至少需要 8 位。"
    elif password != confirm_password:
        error = "两次输入的密码不一致。"

    # 检查不通过：重新显示页面，传入错误提示和已填的账号。
    if error:
        return templates.TemplateResponse(
            request=request,
            name="setup.html",
            context={
                "page_title": "初始化 · 山雀群管",
                "csrf_token": get_csrf_token(request),
                "error": error,
                "username": username,
            },
            status_code=400,
        )

    # 检查通过：将密码转换为哈希，然后保存管理员。
    try:
        await create_admin(
            username=username,
            password_hash=hash_password(password),
        )
    except IntegrityError:
        # 另一个请求可能已经创建了管理员。
        if await has_admin():
            return RedirectResponse("/login", status_code=303)

        raise

    # 创建成功，清除初始化会话。
    request.session.clear()

    # 跳转到登录页面。
    return RedirectResponse("/login", status_code=303)


# 打开登录页面
@router.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    # 没有管理员，先去创建。
    if not await has_admin():
        return RedirectResponse("/setup", status_code=303)

    return templates.TemplateResponse(
        request=request,
        name="login.html",
        context={
            "page_title": "登录 · 山雀群管",
        },
    )
