from typing import Optional, Dict
from pathlib import Path
from fastapi import Depends, HTTPException, Request
from fastapi.responses import HTMLResponse
from starlette import status
from jinja2 import Environment, FileSystemLoader, select_autoescape

from .database import SessionLocal
from .models import User, Notification
from .config import IMAGE_DIR

# Jinja2 环境（从 templates/ 加载）
jinja_env = Environment(loader=FileSystemLoader(str(Path('./templates').resolve())), autoescape=select_autoescape(['html']))


def render_template(name: str, **ctx) -> HTMLResponse:
    # 支持可选的 status_code 参数，不破坏现有调用
    status_code = int(ctx.pop('status_code', 200))
    if 'avatar_url' not in ctx:
        ctx['avatar_url'] = _build_avatar_url(ctx.get('current_user'))
    # 全局未读通知（当前用户）
    cu = ctx.get('current_user')
    if cu:
        try:
            from .database import SessionLocal
            with SessionLocal() as db:
                unread = db.query(Notification).filter(Notification.user_id == cu.id, Notification.is_deleted == False, Notification.read_at == None).order_by(Notification.created_at.desc()).limit(5).all()
                ctx['notifications'] = unread
                ctx['unread_count'] = db.query(Notification).filter(Notification.user_id == cu.id, Notification.is_deleted == False, Notification.read_at == None).count()
        except Exception:
            ctx['notifications'] = []
            ctx['unread_count'] = 0
    html = jinja_env.get_template(name).render(**ctx)
    return HTMLResponse(html, status_code=status_code)


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
