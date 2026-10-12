from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from database import init_db, close_db
from routers import auth, pages
from views import BASE_DIR

from starlette.middleware.sessions import SessionMiddleware

from security import load_session_secret


@asynccontextmanager
async def lifespan(app: FastAPI):
    # 启动时初始化数据库。
    await init_db()

    try:
        yield
    finally:
        # 服务关闭时释放连接。
        await close_db()


app = FastAPI(
    title="山雀群管",
    lifespan=lifespan,
)

app.add_middleware(
    SessionMiddleware,
    secret_key=load_session_secret(),
    session_cookie="titguard_session",
    same_site="strict",
    max_age=8 * 60 * 60,
    https_only=False,
)

app.mount(
    "/static",
    StaticFiles(directory=str(BASE_DIR / "static")),
    name="static",
)

app.include_router(pages.router)
app.include_router(auth.router)


if __name__ == "__main__":
    uvicorn.run(
        "main:app",
        host="127.0.0.1",
        port=8765,
        reload=True,
    )
