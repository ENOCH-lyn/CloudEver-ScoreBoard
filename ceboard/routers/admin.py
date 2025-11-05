from datetime import datetime

from fastapi import APIRouter, Depends, Form, HTTPException, Request, UploadFile, File
from fastapi.responses import RedirectResponse, HTMLResponse

from ..deps import get_db, get_current_user, require_admin, render_template
from ..models import Event, Challenge, Submission, SubmissionItem, User
from ..config import TZ, CATEGORIES


router = APIRouter()


@router.get("/admin/review", response_class=HTMLResponse)
def admin_review_list(request: Request, db = Depends(get_db), current_user = Depends(get_current_user)):
    require_admin(current_user)
    # filters
    event_id = request.query_params.get("event_id")
    q = (request.query_params.get("q") or "").strip()
    sub_q = db.query(Submission)
    if event_id and event_id.isdigit():
        sub_q = sub_q.filter(Submission.event_id == int(event_id))
    subs = sub_q.all()
    rows = []
    for s in subs:
        if q and s.user and (q.lower() not in s.user.username.lower()):
            continue
        pending = sum(1 for it in s.items if not it.approved)
        ok = sum(1 for it in s.items if it.approved and not it.revoked)
        rev = sum(1 for it in s.items if it.revoked)
        rows.append({
            "sub_id": s.id,
            "created_at": s.created_at,
            "username": s.user.username if s.user else f"uid:{s.user_id}",
            "event_name": s.event.name if s.event else "—",
            "pending": pending,
            "ok": ok,
            "rev": rev,
        })
    rows.sort(key=lambda r: r["created_at"], reverse=True)
    events = db.query(Event).order_by(Event.id.desc()).all()
    eid = int(event_id) if event_id and event_id.isdigit() else None
    return render_template("admin_review.html", title="审核中心", current_user=current_user, rows=rows, events=events, event_id=eid, q=q)


@router.get("/admin/review/{sub_id}", response_class=HTMLResponse)
def admin_review_detail(sub_id: int, request: Request, db = Depends(get_db), current_user = Depends(get_current_user)):
    require_admin(current_user)
    sub = db.get(Submission, sub_id)
    if not sub:
        raise HTTPException(404, "提交不存在")
    items = db.query(SubmissionItem).filter(SubmissionItem.submission_id == sub_id).all()
    return render_template("admin_review_detail.html", title="审核提交", current_user=current_user, sub=sub, user=sub.user, event=sub.event, items=items)


@router.post("/admin/review/{sub_id}/approve_all")
def admin_review_approve_all(sub_id: int, db = Depends(get_db), current_user = Depends(get_current_user)):
    require_admin(current_user)
    items = db.query(SubmissionItem).filter(SubmissionItem.submission_id == sub_id).all()
    for it in items:
        if not it.approved:
            it.approved = True
    db.commit()
    return RedirectResponse(f"/admin/review/{sub_id}?msg=全部通过", status_code=302)


@router.post("/admin/review/event/{event_id}/approve_all")
def admin_review_approve_event_all(event_id: int, db = Depends(get_db), current_user = Depends(get_current_user)):
    require_admin(current_user)
    items = (
        db.query(SubmissionItem)
        .join(Submission, Submission.id == SubmissionItem.submission_id)
        .filter(Submission.event_id == event_id, SubmissionItem.approved == False)
        .all()
    )
    for it in items:
        it.approved = True
    db.commit()
    return RedirectResponse(f"/admin/review?event_id={event_id}&msg=已通过该活动全部待审", status_code=302)


@router.post("/admin/review/item/{item_id}/toggle_approve")
def admin_toggle_approve(item_id: int, db = Depends(get_db), current_user = Depends(get_current_user)):
    require_admin(current_user)
    it = db.get(SubmissionItem, item_id)
    if not it:
        raise HTTPException(404, "条目不存在")
    it.approved = not it.approved
    if not it.approved:
        it.revoked = False
    db.commit()
    return RedirectResponse(f"/admin/review/{it.submission_id}?msg=已切换通过状态", status_code=302)


@router.post("/admin/review/item/{item_id}/toggle_revoke")
def admin_toggle_revoke(item_id: int, db = Depends(get_db), current_user = Depends(get_current_user)):
    require_admin(current_user)
    it = db.get(SubmissionItem, item_id)
    if not it:
        raise HTTPException(404, "条目不存在")
    if it.approved:
        it.revoked = not it.revoked
    db.commit()
    return RedirectResponse(f"/admin/review/{it.submission_id}?msg=已切换撤销状态", status_code=302)


@router.get("/admin/events", response_class=HTMLResponse)
def admin_events(request: Request, db = Depends(get_db), current_user = Depends(get_current_user)):
    require_admin(current_user)
    events = db.query(Event).all()
    now = datetime.now(TZ)
    return render_template("admin_events.html", title="活动管理", current_user=current_user, events=events, year=now.year, month=now.month)


@router.post("/admin/events/create")
def admin_create_event(request: Request, name: str = Form(...), start: str = Form(""), end: str = Form(""), weight: float = Form(1.0), is_reproduction: int = Form(0), db = Depends(get_db), current_user = Depends(get_current_user)):
    require_admin(current_user)

    def parse_dt(s: str):
        s = (s or "").strip()
        if not s:
            return None
        try:
            # support both "YYYY-MM-DD HH:MM" and HTML datetime-local "YYYY-MM-DDTHH:MM"
            if "T" in s:
                return datetime.strptime(s, "%Y-%m-%dT%H:%M").replace(tzinfo=TZ)
            return datetime.strptime(s, "%Y-%m-%d %H:%M").replace(tzinfo=TZ)
        except Exception:
            return None

    evt = Event(
        name=name.strip(),
        start_time=parse_dt(start),
        end_time=parse_dt(end),
        weight=weight,
        is_reproduction=bool(int(is_reproduction)),
        is_active=True,
    )
    db.add(evt)
    db.commit()
    return RedirectResponse("/admin/events?msg=已创建", status_code=302)


@router.get("/admin/events/{event_id}/edit", response_class=HTMLResponse)
def admin_edit_event(event_id: int, request: Request, db = Depends(get_db), current_user = Depends(get_current_user)):
    require_admin(current_user)
    event = db.get(Event, event_id)
    if not event:
        raise HTTPException(404, "活动不存在")
    return render_template("admin_event_edit.html", title="编辑活动", current_user=current_user, event=event)


@router.post("/admin/events/{event_id}/edit")
def admin_update_event(event_id: int, name: str = Form(...), start: str = Form(""), end: str = Form(""), weight: float = Form(1.0), is_reproduction: int = Form(0), db = Depends(get_db), current_user = Depends(get_current_user)):
    require_admin(current_user)

    def parse_dt(s: str):
        s = (s or "").strip()
        if not s:
            return None
        try:
            if "T" in s:
                return datetime.strptime(s, "%Y-%m-%dT%H:%M").replace(tzinfo=TZ)
            return datetime.strptime(s, "%Y-%m-%d %H:%M").replace(tzinfo=TZ)
        except Exception:
            return None

    evt = db.get(Event, event_id)
    if not evt:
        raise HTTPException(404, "活动不存在")
    evt.name = name.strip()
    evt.start_time = parse_dt(start)
    evt.end_time = parse_dt(end)
    evt.weight = weight
    evt.is_reproduction = bool(int(is_reproduction))
    db.commit()
    return RedirectResponse("/admin/events?msg=已保存", status_code=302)


@router.post("/admin/events/{event_id}/toggle_active")
def admin_toggle_event_active(event_id: int, db = Depends(get_db), current_user = Depends(get_current_user)):
    require_admin(current_user)
    evt = db.get(Event, event_id)
    if not evt:
        raise HTTPException(404, "活动不存在")
    evt.is_active = not evt.is_active
    db.commit()
    return RedirectResponse("/admin/events?msg=已切换状态", status_code=302)


@router.post("/admin/events/{event_id}/delete")
def admin_delete_event(event_id: int, db = Depends(get_db), current_user = Depends(get_current_user)):
    require_admin(current_user)
    evt = db.get(Event, event_id)
    if not evt:
        raise HTTPException(404, "活动不存在")
    # 允许删除：同时删除该活动的提交与题目
    subs = db.query(Submission).filter(Submission.event_id == event_id).all()
    sub_count = len(subs)
    for s in subs:
        db.delete(s)
    db.delete(evt)
    db.commit()
    return RedirectResponse(f"/admin/events?msg=已删除(含{sub_count}条提交)", status_code=302)


@router.get("/admin/events/{event_id}", response_class=HTMLResponse)
def admin_event_detail(event_id: int, request: Request, db = Depends(get_db), current_user = Depends(get_current_user)):
    require_admin(current_user)
    event = db.get(Event, event_id)
    if not event:
        raise HTTPException(404, "活动不存在")
    users = db.query(User).order_by(User.username.asc()).all()
    rows = []
    for u in users:
        subs = db.query(Submission).filter(Submission.user_id == u.id, Submission.event_id == event_id).all()
        total_items = sum(len(s.items) for s in subs)
        if total_items == 0:
            continue
        approved = sum(1 for s in subs for it in s.items if it.approved and not it.revoked)
        pending = sum(1 for s in subs for it in s.items if not it.approved)
        revoked = sum(1 for s in subs for it in s.items if it.revoked)
        rows.append({"user": u, "approved": approved, "pending": pending, "revoked": revoked, "subs": subs})
    return render_template("admin_event_detail.html", title=f"活动详情 — {event.name}", current_user=current_user, event=event, rows=rows)


@router.get("/admin/events/{event_id}/challenges", response_class=HTMLResponse)
def admin_event_challenges(event_id: int, request: Request, db = Depends(get_db), current_user = Depends(get_current_user)):
    require_admin(current_user)
    event = db.get(Event, event_id)
    if not event:
        raise HTTPException(404, "活动不存在")
    q = (request.query_params.get("q") or "").strip()
    cat = request.query_params.get("cat") or ""
    ch_q = db.query(Challenge).filter(Challenge.event_id == event_id)
    if cat:
        ch_q = ch_q.filter(Challenge.category == cat)
    if q:
        ch_q = ch_q.filter(Challenge.name.contains(q))
    challenges = ch_q.all()
    return render_template("admin_challenges.html", title="题目管理", current_user=current_user, event=event, challenges=challenges, categories=CATEGORIES, msg=request.query_params.get("msg"), q=q, cat=cat)


@router.post("/admin/events/{event_id}/challenges/add")
def admin_add_challenge(event_id: int, name: str = Form(...), category: str = Form("misc"), base_score: int = Form(100), db = Depends(get_db), current_user = Depends(get_current_user)):
    require_admin(current_user)
    event = db.get(Event, event_id)
    if not event:
        raise HTTPException(404, "活动不存在")
    ch = Challenge(event_id=event_id, name=name.strip(), category=category.strip(), base_score=int(base_score))
    db.add(ch)
    db.commit()
    return RedirectResponse(f"/admin/events/{event_id}/challenges?msg=已添加", status_code=302)


@router.post("/admin/events/{event_id}/challenges/{ch_id}/delete")
def admin_delete_challenge(event_id: int, ch_id: int, db = Depends(get_db), current_user = Depends(get_current_user)):
    require_admin(current_user)
    event = db.get(Event, event_id)
    if not event:
        raise HTTPException(404, "活动不存在")
    ch = db.get(Challenge, ch_id)
    if not ch or ch.event_id != event_id:
        raise HTTPException(404, "题目不存在")

    refcount = db.query(SubmissionItem).filter(SubmissionItem.challenge_id == ch_id).count()
    if refcount > 0:
        return RedirectResponse(f"/admin/events/{event_id}/challenges?msg=该题目已被{refcount}条提交引用，不能删除", status_code=302)

    db.delete(ch)
    db.commit()
    return RedirectResponse(f"/admin/events/{event_id}/challenges?msg=已删除", status_code=302)


@router.get("/admin/users", response_class=HTMLResponse)
def admin_users(request: Request, db = Depends(get_db), current_user = Depends(get_current_user)):
    require_admin(current_user)
    users = db.query(User).order_by(User.username.asc()).all()
    return render_template("admin_users.html", title="成员管理", current_user=current_user, users=users)


@router.get("/admin/users/{uid}", response_class=HTMLResponse)
def admin_user_detail(uid: int, request: Request, db = Depends(get_db), current_user = Depends(get_current_user)):
    require_admin(current_user)
    u = db.get(User, uid)
    if not u:
        raise HTTPException(404, "用户不存在")
    subs = db.query(Submission).filter(Submission.user_id == uid).all()
    total_items = sum(len(s.items) for s in subs)
    approved = sum(1 for s in subs for it in s.items if it.approved and not it.revoked)
    pending = sum(1 for s in subs for it in s.items if not it.approved)
    revoked = sum(1 for s in subs for it in s.items if it.revoked)
    return render_template("admin_user_detail.html", title=f"成员详情 — {u.username}", current_user=current_user, user=u, subs=subs, total_items=total_items, approved=approved, pending=pending, revoked=revoked)


@router.post("/admin/users/{uid}/password")
def admin_set_user_password(uid: int, new_password: str = Form(...), db = Depends(get_db), current_user = Depends(get_current_user)):
    from passlib.hash import pbkdf2_sha256 as pwdhash
    require_admin(current_user)
    if len(new_password or "") < 6:
        return RedirectResponse(f"/admin/users/{uid}?msg=密码至少6位", status_code=302)
    u = db.get(User, uid)
    if not u:
        raise HTTPException(404, "用户不存在")
    u.password_hash = pwdhash.hash(new_password)
    db.add(u); db.commit()
    return RedirectResponse(f"/admin/users/{uid}?msg=密码已更新", status_code=302)


@router.post("/admin/users/{uid}/avatar")
async def admin_set_user_avatar(uid: int, file: UploadFile = File(...), db = Depends(get_db), current_user = Depends(get_current_user)):
    from pathlib import Path
    from ..config import MAX_AVATAR_SIZE, IMAGE_DIR
    require_admin(current_user)
    u = db.get(User, uid)
    if not u:
        raise HTTPException(404, "用户不存在")
    content_type = (file.content_type or '').lower()
    ext_map = {
        'image/png': '.png',
        'image/jpeg': '.jpg',
        'image/webp': '.webp',
    }
    if content_type not in ext_map:
        return RedirectResponse(f"/admin/users/{uid}?msg=仅支持 PNG/JPG/WebP", status_code=302)
    data = await file.read()
    if len(data) > MAX_AVATAR_SIZE:
        return RedirectResponse(f"/admin/users/{uid}?msg=文件过大(>1MB)", status_code=302)
    ext = ext_map[content_type]
    safe_name = f"u_{u.id}{ext}"
    out_path = Path(IMAGE_DIR) / safe_name
    try:
        with open(out_path, 'wb') as f:
            f.write(data)
    except Exception:
        return RedirectResponse(f"/admin/users/{uid}?msg=保存失败(权限或磁盘)", status_code=302)
    u.avatar_filename = safe_name
    db.add(u); db.commit()
    return RedirectResponse(f"/admin/users/{uid}?msg=头像已更新", status_code=302)


@router.post("/admin/users/{uid}/update")
def admin_update_user(uid: int, role: str = Form(...), team_type: str = Form(...), db = Depends(get_db), current_user = Depends(get_current_user)):
    require_admin(current_user)
    u = db.get(User, uid)
    if not u:
        raise HTTPException(404, "用户不存在")
    u.role = role if role in ("member", "admin") else u.role
    u.team_type = team_type if team_type in ("main", "sub") else u.team_type
    db.commit()
    return RedirectResponse("/admin/users?msg=已更新", status_code=302)


# 按月清空提交功能已移除：保留历史数据，月榜通过时间范围统计实现
