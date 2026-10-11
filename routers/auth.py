import re
import sqlite3

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from database import create_admin, has_admin
from security import (
    get_csrf_token,
    hash_password,
    valid_csrf_token,
)
from views import templates


# 创建路由对象，供 main.py 注册。
router = APIRouter()


# 公共函数：显示初始化页面。
# 第一次打开页面、提交出错时，都可以调用它。
def render_setup(
    request: Request,
    error: str = "",          # 错误提示，默认没有错误
    username: str = "",       # 回填账号，避免用户重新输入
    status_code: int = 200,   # 200 表示请求成功
):
    return templates.TemplateResponse(
        request=request,
        name="setup.html",
        # context 中的数据可以在 HTML 中通过 {{ 变量名 }} 使用。
        context={
            "page_title": "初始化 · 山雀群管",
            "csrf_token": get_csrf_token(request),
            "error": error,
            "username": username,
        },
        status_code=status_code,
    )


# GET /setup：用户打开初始化页面时执行。
@router.get("/setup", response_class=HTMLResponse)
def setup_page(request: Request):
    # 已经创建过管理员，就不允许再次初始化。
    if has_admin():
        return RedirectResponse("/login", status_code=303)

    return render_setup(request)


# POST /setup：用户点击“创建管理员”提交表单时执行。
@router.post("/setup", response_class=HTMLResponse)
def setup_submit(
    request: Request,
    # Form(...) 表示从表单读取必填字段。
    # 参数名必须与 HTML 输入框的 name 属性一致。
    username: str = Form(...),
    password: str = Form(...),
    confirm_password: str = Form(...),
    csrf_token: str = Form(...),
):
    # 提交时也检查，防止绕过页面重复创建管理员。
    if has_admin():
        return RedirectResponse("/login", status_code=303)

    # 检查表单中的校验码是否与当前会话一致。
    # 不一致则拒绝请求，403 表示禁止访问。
    if not valid_csrf_token(request, csrf_token):
        raise HTTPException(
            status_code=403,
            detail="表单已过期，请刷新页面后重试。",
        )

    # 去掉账号两端的空格。
    # 密码不做 strip()，因为空格也可能是密码的一部分。
    username = username.strip()

    error = ""

    # fullmatch 要求整个账号都符合规则：
    # A-Z、a-z：英文字母
    # 0-9：数字
    # _：下划线
    # {3,32}：长度为 3～32 位
    if not re.fullmatch(r"[A-Za-z0-9_]{3,32}", username):
        error = "账号需为 3～32 位字母、数字或下划线。"

    # 密码只要求至少 8 位。
    elif len(password) < 8:
        error = "密码至少需要 8 位。"

    # 检查两次输入是否一致。
    elif password != confirm_password:
        error = "两次输入的密码不一致。"

    # 有错误就重新显示初始化页面，并展示错误原因。
    # 只回填账号，不把密码传回页面。
    if error:
        return render_setup(
            request,
            error=error,
            username=username,
            status_code=400,  # 400 表示提交的数据有问题
        )

    try:
        # 先把密码转换为哈希，再保存到数据库。
        # 数据库中不保存原始密码。
        create_admin(
            username=username,
            password_hash=hash_password(password),
        )
    except sqlite3.IntegrityError:
        # 如果两个请求同时创建管理员，数据库只允许一个成功。
        # 确认管理员已经存在后，跳转到登录页面。
        if has_admin():
            return RedirectResponse("/login", status_code=303)

        # 如果是其他数据库约束错误，则交给程序报告。
        raise

    # 清除初始化时的会话信息。
    # 这里只完成创建账号，还没有登录。
    request.session.clear()

    # 303 告诉浏览器用 GET 请求打开登录页，
    # 避免刷新页面时重复提交创建管理员的表单。
    return RedirectResponse("/login", status_code=303)


# GET /login：显示登录页面，暂时不处理账号密码验证。
@router.get("/login", response_class=HTMLResponse)
def login_page(request: Request):
    # 还没有管理员时，必须先初始化。
    if not has_admin():
        return RedirectResponse("/setup", status_code=303)

    return templates.TemplateResponse(
        request=request,
        name="login.html",
        context={
            "page_title": "登录 · 山雀群管",
        },
    )