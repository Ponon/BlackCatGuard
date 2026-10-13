from contextlib import asynccontextmanager
import asyncio
import uvicorn
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from dao.database import init_db, close_db
from routers import auth, pages, group, settings, verification, antispam, events, broadcast
from utils.views import BASE_DIR
from starlette.middleware.sessions import SessionMiddleware
from utils.security import load_session_secret
from services.broadcast_scheduler import run_scheduler
from services.telegram_worker import run_telegram_worker
from services.runtime_health import run_health_monitor
from services.background import supervise
from services.moderation import run_moderation_worker
from services.notifications import run_push_worker
from services.log_retention import run_log_cleanup


# 生命周期管理
@asynccontextmanager
async def lifespan(app: FastAPI):
    # 启动时初始化数据库。
    await init_db()
    workers = [
        asyncio.create_task(supervise(service, name), name=name)
        for service, name in (
            (run_health_monitor, 'health-monitor'),
            (run_telegram_worker, 'telegram-worker'),
            (run_scheduler, 'broadcast-scheduler'),
            (run_moderation_worker, 'moderation-worker'),
            (run_push_worker, 'push-worker'),
            (run_log_cleanup, 'log-cleanup'),
        )
    ]
    try:
        yield
    finally:
        for worker in workers:
            worker.cancel()
        await asyncio.gather(*workers, return_exceptions=True)
        await close_db()


# 创建实例
app = FastAPI(
    title="黑猫群管",
    lifespan=lifespan,
)


# 添加会话中间件
app.add_middleware(
    SessionMiddleware,
    secret_key=load_session_secret(),
    session_cookie="blackcatguard_session",
    same_site="strict",
    max_age=8 * 60 * 60,
    https_only=False,
)


# 挂载静态文件目录
app.mount(
    "/static",
    StaticFiles(directory=str(BASE_DIR / "static")),
    name="static",
)


# 挂载路由
app.include_router(pages.router)
app.include_router(auth.router)
app.include_router(group.router)
app.include_router(settings.router)
app.include_router(verification.router)
app.include_router(antispam.router)
app.include_router(events.router)
app.include_router(broadcast.router)


# 主程序
if __name__ == "__main__":
    uvicorn.run("main:app", host="127.0.0.1", port=1777, reload=True )
