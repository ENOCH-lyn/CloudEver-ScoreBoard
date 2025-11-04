from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse, HTMLResponse
from passlib.hash import pbkdf2_sha256 as pwdhash

from ..deps import get_db, get_current_user, render_template
from ..models import User

router = APIRouter()

@router.get("/auth/login", response_class=HTMLResponse)
def login_page(request: Request, current_user = Depends(get_current_user)):
    if current_user:
        return RedirectResponse("/", status_code=302)
    return render_template("login.html", title="登录", current_user=None, msg=request.query_params.get("msg"))


@router.post("/auth/login")
def do_login(request: Request, username: str = Form(...), password: str = Form(...), db = Depends(get_db)):
    user = db.query(User).filter(User.username == username).first()
    if not user or not pwdhash.verify(password, user.password_hash):
        return RedirectResponse("/auth/login?msg=账号或密码错误", status_code=302)
    request.session["user_id"] = user.id
    return RedirectResponse("/", status_code=302)


@router.post("/auth/logout")
def do_logout(request: Request):
    request.session.clear()
    return RedirectResponse("/auth/login?msg=已退出登录", status_code=302)


@router.get("/auth/register", response_class=HTMLResponse)
def register_page(request: Request, current_user = Depends(get_current_user)):
    if current_user:
        return RedirectResponse("/", status_code=302)
    return render_template("register.html", title="注册", current_user=None, msg=request.query_params.get("msg"))


@router.post("/auth/register")
def do_register(request: Request, username: str = Form(...), password: str = Form(...), password2: str = Form(...), db = Depends(get_db)):
    if password != password2:
        return RedirectResponse("/auth/register?msg=两次密码不一致", status_code=302)
    if db.query(User).filter(User.username == username).first():
        return RedirectResponse("/auth/register?msg=用户名已存在", status_code=302)
    u = User(username=username.strip(), password_hash=pwdhash.hash(password), role="member", team_type="sub")
    db.add(u); db.commit()
    return RedirectResponse("/auth/login?msg=注册成功, 请登录", status_code=302)
