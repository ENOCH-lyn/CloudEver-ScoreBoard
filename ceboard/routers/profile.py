from pathlib import Path
from fastapi import APIRouter, Depends, Form, UploadFile, File, Request
from fastapi.responses import RedirectResponse, HTMLResponse

from ..deps import get_db, get_current_user, render_template, require_login
from ..models import User
from ..config import MAX_AVATAR_SIZE, IMAGE_DIR

router = APIRouter()

@router.get("/profile", response_class=HTMLResponse)
def profile_page(request: Request, current_user = Depends(get_current_user)):
    require_login(current_user)
    return render_template("profile.html", title="个人设置", current_user=current_user)


@router.post("/profile/password")
def change_password(request: Request, old_password: str = Form(...), new_password: str = Form(...), new_password2: str = Form(...), db = Depends(get_db), current_user = Depends(get_current_user)):
    from passlib.hash import pbkdf2_sha256 as pwdhash
    require_login(current_user)
    if new_password != new_password2:
        return RedirectResponse("/profile?msg=两次新密码不一致", status_code=302)
    from ..models import User
    if not current_user or not pwdhash.verify(old_password, current_user.password_hash):
        return RedirectResponse("/profile?msg=当前密码错误", status_code=302)
    if len(new_password) < 6:
        return RedirectResponse("/profile?msg=新密码长度至少6位", status_code=302)
    current_user.password_hash = pwdhash.hash(new_password)
    db.add(current_user); db.commit()
    return RedirectResponse("/profile?msg=密码已更新", status_code=302)


@router.post("/profile/avatar")
async def upload_avatar(request: Request, file: UploadFile = File(...), db = Depends(get_db), current_user = Depends(get_current_user)):
    require_login(current_user)
    content_type = (file.content_type or '').lower()
    ext_map = {
        'image/png': '.png',
        'image/jpeg': '.jpg',
        'image/webp': '.webp',
    }
    if content_type not in ext_map:
        return RedirectResponse("/profile?msg=仅支持 PNG/JPG/WebP", status_code=302)
    data = await file.read()
    if len(data) > MAX_AVATAR_SIZE:
        return RedirectResponse("/profile?msg=文件过大(>1MB)", status_code=302)
    ext = ext_map[content_type]
    safe_name = f"u_{current_user.id}{ext}"
    out_path = Path(IMAGE_DIR) / safe_name
    try:
        with open(out_path, 'wb') as f:
            f.write(data)
    except Exception:
        return RedirectResponse("/profile?msg=保存失败(权限或磁盘)", status_code=302)
    current_user.avatar_filename = safe_name
    db.add(current_user); db.commit()
    return RedirectResponse("/profile?msg=头像已更新", status_code=302)
