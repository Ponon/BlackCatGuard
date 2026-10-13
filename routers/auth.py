import re
import time
from tortoise.exceptions import IntegrityError
from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from dao.models import Administrator
from utils.security import (
    get_csrf_token,
    hash_password,
    valid_csrf_token,
    verify_password,
)
from utils.views import templates

router = APIRouter()
TERMS_VERSION = "2026-10-13"
CONSENT_MAX_AGE = 3600


def has_setup_consent(request):
    consent = request.session.get("setup_consent", {})
    if not isinstance(consent, dict):
        return False
    accepted_at = consent.get("accepted_at")
    return (consent.get("version") == TERMS_VERSION
            and isinstance(accepted_at, (int, float))
            and 0 <= time.time() - accepted_at <= CONSENT_MAX_AGE)


@router.post("/setup/consent")
async def setup_consent(request: Request, csrf_token: str = Form(...),
                        accepted: str = Form(""), terms_version: str = Form("")):
    if await Administrator.exists():
        return RedirectResponse("/login", status_code=303)
    if not valid_csrf_token(request, csrf_token):
        raise HTTPException(status_code=403, detail="表单已过期请刷新页面后重试")
    if accepted != "yes" or terms_version != TERMS_VERSION:
        if "application/json" in request.headers.get("accept", ""):
            return JSONResponse({"error": "请阅读并同意当前使用须知"}, status_code=400)
        return templates.TemplateResponse(request=request, name="setup.html", context={
            "page_title": "初始化 - 黑猫群管", "csrf_token": get_csrf_token(request),
            "error": "请阅读并同意当前使用须知", "username": "",
            "step": "terms", "terms_version": TERMS_VERSION,
        }, status_code=400)
    request.session["setup_consent"] = {"version": TERMS_VERSION, "accepted_at": time.time()}
    if "application/json" in request.headers.get("accept", ""):
        return JSONResponse({"redirect": "/setup?step=create"})
    return RedirectResponse("/setup?step=create", status_code=303)



# 打开初始化页面
@router.get("/setup", response_class=HTMLResponse)
async def setup_page(request: Request):
    # 已有管理员，跳转到登录页面。
    if await Administrator.exists():
        return RedirectResponse("/login", status_code=303)

    # 没有管理员，显示创建管理员页面。
    return templates.TemplateResponse(
        request=request,
        name="setup.html",
        context={
            "page_title": "初始化 - 黑猫群管",
            "csrf_token": get_csrf_token(request),
            "error": "",
            "username": "",
            "terms_version": TERMS_VERSION,
            "step": "create" if request.query_params.get("step") == "create" and has_setup_consent(request) else "terms",
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
    if await Administrator.exists():
        return RedirectResponse("/login", status_code=303)

    # 验证表单校验码。
    if not valid_csrf_token(request, csrf_token):
        raise HTTPException(
            status_code=403,
            detail="表单已过期，请刷新页面后重试。",
        )

    if not has_setup_consent(request):
        if "application/json" in request.headers.get("accept", ""):
            return JSONResponse({"error": "请先阅读并同意使用须知", "redirect": "/setup"}, status_code=403)
        return RedirectResponse("/setup", status_code=303)

    # 去掉账号前后的空格，密码保持原样。
    username = username.strip()

    error = ""

    # 按顺序检查账号、密码长度和确认密码。
    if not re.fullmatch(r"[A-Za-z0-9_]{3,32}", username):
        error = "账号需为 3～32 位字母、数字或下划线。"
    elif len(password) < 8:
        error = "密码至少需要 8 位。"
    elif len(password) > 128:
        error = "密码最多允许 128 位"
    elif password != confirm_password:
        error = "两次输入的密码不一致。"

    # 检查不通过：重新显示页面，传入错误提示和已填的账号。
    if error:
        # 异步提交只返回错误信息，浏览器无需刷新整页。
        if "application/json" in request.headers.get("accept", ""):
            return JSONResponse({"error": error}, status_code=400)
        return templates.TemplateResponse(
            request=request,
            name="setup.html",
            context={
                "page_title": "初始化 - 黑猫群管",
                "csrf_token": get_csrf_token(request),
                "error": error,
                "username": username,
                "step": "create",
                "terms_version": TERMS_VERSION,
            },
            status_code=400,
        )

    # 检查通过：将密码转换为哈希，然后保存管理员。
    try:
        await Administrator.create(
            id=1,
            username=username,
            password_hash=hash_password(password),
        )
    except IntegrityError:
        # 另一个请求可能已经创建了管理员。
        if await Administrator.exists():
            return RedirectResponse("/login", status_code=303)

        raise

    # 创建成功，清除初始化会话。
    request.session.clear()

    # 异步提交由前端保存一次性成功提示，再跳转登录页。
    if "application/json" in request.headers.get("accept", ""):
        return JSONResponse({"message": "管理员创建成功请登录", "redirect": "/login"})

    request.session["success_message"] = "管理员创建成功请登录"
    # 跳转到登录页面。
    return RedirectResponse("/login", status_code=303)


# 打开登录页面
@router.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    # 没有管理员，先去创建。
    if not await Administrator.exists():
        return RedirectResponse("/setup", status_code=303)

    # 会话中的管理员仍然存在，说明已登录，直接进入首页。
    if await Administrator.filter(id=request.session.get("admin_id")).exists():
        return RedirectResponse("/", status_code=303)

    return templates.TemplateResponse(
        request=request,
        name="login.html",
        context={
            "page_title": "登录 - 黑猫群管",
            "csrf_token": get_csrf_token(request),
            # 普通表单提交的提示读取后即删除，刷新不会重复显示。
            "success": request.session.pop("success_message", ""),
            "error": "",
            "username": "",
        },
    )


# 接收登录表单，验证成功后保存登录状态。
@router.post("/login", response_class=HTMLResponse)
async def login_submit(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    csrf_token: str = Form(...),
):
    # 尚未创建管理员时，先完成初始化。
    if not await Administrator.exists():
        return RedirectResponse("/setup", status_code=303)

    # 校验表单令牌，防止其他网站伪造登录请求。
    if not valid_csrf_token(request, csrf_token):
        raise HTTPException(status_code=403, detail="表单已过期，请刷新页面后重试。")

    # 账号去掉首尾空格，密码保持用户输入的原样。
    username = username.strip()
    admin = await Administrator.get_or_none(username=username)
    # 账号不存在和密码错误使用相同提示，避免暴露账号是否存在。
    if admin is None or not verify_password(password, admin.password_hash):
        if "application/json" in request.headers.get("accept", ""):
            return JSONResponse({"error": "账号或密码错误。"}, status_code=401)
        # 保留账号供用户重试，不把密码传回页面。
        return templates.TemplateResponse(
            request=request,
            name="login.html",
            context={
                "page_title": "登录 - 黑猫群管",
                "csrf_token": get_csrf_token(request),
                "error": "账号或密码错误。",
                "username": username,
            },
            status_code=401,
        )

    # 清除旧会话，登录后只保存管理员身份。
    request.session.clear()
    request.session["admin_id"] = admin.id
    # 异步提交让首页显示一次成功提示，避免 fetch 跟随跳转时提前消耗提示。
    if "application/json" in request.headers.get("accept", ""):
        return JSONResponse({"message": "登录成功", "redirect": "/"})
    request.session["success_message"] = "登录成功"
    # 使用 303 跳转，让浏览器以 GET 请求打开首页，避免重复提交表单。
    return RedirectResponse("/", status_code=303)


# 退出登录表单，清除当前浏览器的登录会话。
@router.post("/logout")
async def logout_submit(request: Request, csrf_token: str = Form(...)):
    # 校验首页生成的令牌，防止其他网站伪造退出请求。
    if not valid_csrf_token(request, csrf_token):
        raise HTTPException(status_code=403, detail="表单已过期，请刷新页面后重试。")

    # 同时移除管理员身份和旧 CSRF 令牌。
    request.session.clear()
    # 登录页读取后即删除，刷新不会重复弹出退出提示。
    request.session["success_message"] = "已退出登录"
    return RedirectResponse("/login", status_code=303)

