from typing import Optional, Dict
from pathlib import Path
from fastapi import Depends, HTTPException, Request
from fastapi.responses import HTMLResponse
from starlette import status
from jinja2 import Environment, FileSystemLoader, select_autoescape

from .database import SessionLocal
from .models import User
from .config import IMAGE_DIR

# Jinja2 环境（从 templates/ 加载）
jinja_env = Environment(loader=FileSystemLoader(str(Path('./templates').resolve())), autoescape=select_autoescape(['html']))


def render_template(name: str, **ctx) -> HTMLResponse:
    if 'avatar_url' not in ctx:
        ctx['avatar_url'] = _build_avatar_url(ctx.get('current_user'))
    html = jinja_env.get_template(name).render(**ctx)
    return HTMLResponse(html)


def _build_avatar_url(user: Optional[User]) -> Optional[str]:
    try:
        if user and getattr(user, 'avatar_filename', None):
            return f"/images/{user.avatar_filename}"
    except Exception:
        return None
    return None


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def get_current_user(request: Request, db = Depends(get_db)) -> Optional[User]:
    uid = request.session.get("user_id")
    return db.get(User, uid) if uid else None


def require_login(user: Optional[User]):
    if not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="需要先登录")


def require_admin(user: Optional[User]):
    require_login(user)
    if user.role != "admin":
        raise HTTPException(status_code=403, detail="需要管理员权限")


def require_admin_or_reviewer(user: Optional[User]):
    require_login(user)
    if user.role not in ("admin", "reviewer"):
        raise HTTPException(status_code=403, detail="需要审核员或管理员权限")


async def await_form(request: Request) -> Dict[str, str]:
    form = await request.form()
    return {k: (v if isinstance(v, str) else getattr(v, 'filename', str(v))) for k, v in form.items()}
