from contextlib import asynccontextmanager
import uvicorn
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from dao.database import init_db, close_db
from routers import auth, pages, group, settings, verification
from utils.views import BASE_DIR
from starlette.middleware.sessions import SessionMiddleware
from utils.security import load_session_secret


# 生命周期管理
@asynccontextmanager
async def lifespan(app: FastAPI):
    # 启动时初始化数据库。
    await init_db()

    try:
        yield
    finally:
        # 服务关闭时释放连接。
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


# 主程序
if __name__ == "__main__":
    uvicorn.run("main:app", host="127.0.0.1", port=8765, reload=True )
