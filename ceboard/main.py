from fastapi import FastAPI, HTTPException, Request
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware
from fastapi.responses import RedirectResponse
from fastapi.exception_handlers import http_exception_handler as default_http_exception_handler

from .config import IMAGE_DIR, SESSION_SECRET
from .database import init_db_and_migrate, SessionLocal
from .models import User
from passlib.hash import pbkdf2_sha256 as pwdhash

from .routers import auth, profile, public, submit, admin
from contextlib import asynccontextmanager


app = FastAPI(title="CTF 战队考核系统")
app.add_middleware(SessionMiddleware, secret_key=SESSION_SECRET)
app.mount("/images", StaticFiles(directory=IMAGE_DIR), name="images")


@asynccontextmanager
async def lifespan(app):
    # 初始化数据库和轻量迁移
    init_db_and_migrate()
    # 默认管理员账号（若无用户时）
    with SessionLocal() as db:
        if db.query(User).count() == 0:
            db.add(User(
                username="admin",
                password_hash=pwdhash.hash("1qaz@WSX"),
                role="admin",
                team_type="main",
            ))
            db.commit()
    yield

# 使用 lifespan 替代已弃用的 @app.on_event("startup")
app.router.lifespan_context = lifespan


# 注册路由模块
app.include_router(auth.router)
app.include_router(profile.router)
app.include_router(public.router)
app.include_router(submit.router)
app.include_router(admin.router)


@app.exception_handler(HTTPException)
async def http_exc_redirect_login(request: Request, exc: HTTPException):
    # 未登录/无权限统一跳转登录（不返回纯 JSON）
    if exc.status_code in (401, 403):
        next_url = request.url.path
        return RedirectResponse(url=f"/auth/login?next={next_url}", status_code=302)
    return await default_http_exception_handler(request, exc)
