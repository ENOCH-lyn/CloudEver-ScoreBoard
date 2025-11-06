from datetime import datetime

from fastapi import APIRouter, Depends, Form, HTTPException, Request, UploadFile, File
from fastapi.responses import RedirectResponse, HTMLResponse

from ..deps import get_db, get_current_user, require_admin, render_template, require_admin_or_reviewer
from ..models import Event, Challenge, Submission, SubmissionItem, User, Announcement, PointAdjustment, EventType
from ..models import Setting
from ..config import TZ, CATEGORIES
from ..utils import compute_submission_points


router = APIRouter()
@router.get("/admin/advanced", response_class=HTMLResponse)
def admin_advanced_dashboard(request: Request, db = Depends(get_db), current_user = Depends(get_current_user)):
    require_admin(current_user)
    # Simple KPIs
    active_events = db.query(Event).filter(Event.is_active == True, Event.is_deleted == False).count()
    recent_subs = db.query(Submission).filter(Submission.is_deleted == False).order_by(Submission.created_at.desc()).limit(10).all()
    now = datetime.now(TZ)
    year, month = now.year, now.month
    # 本月提交次数
    from_date = datetime(year, month, 1, tzinfo=TZ)
    to_date = datetime(year + 1, 1, 1, tzinfo=TZ) if month == 12 else datetime(year, month + 1, 1, tzinfo=TZ)
    month_submissions = db.query(Submission).filter(Submission.is_deleted == False, Submission.created_at >= from_date, Submission.created_at < to_date).count()
    # 本月获得积分数 = 本月提交积分总和 + 本月积分调整总和
    subs_month = db.query(Submission).filter(Submission.is_deleted == False, Submission.created_at >= from_date, Submission.created_at < to_date).all()
    month_points_from_subs = sum(compute_submission_points(s) for s in subs_month)
    month_adjusts = db.query(PointAdjustment).filter(PointAdjustment.year == year, PointAdjustment.month == month, PointAdjustment.is_deleted == False).all()
    month_points_total = month_points_from_subs + sum(float(a.amount) for a in month_adjusts)
    # 主队/子队人数（仅统计活跃且未删除成员）
    main_count = db.query(User).filter(User.team_type == 'main', User.is_active == True, User.is_deleted == False).count()
    sub_count = db.query(User).filter(User.team_type == 'sub', User.is_active == True, User.is_deleted == False).count()
    return render_template(
        "admin_advanced.html",
        title="管理员面板",
        current_user=current_user,
        active_events=active_events,
        recent_subs=recent_subs,
        year=year,
        month=month,
        month_submissions=month_submissions,
        month_points_total=month_points_total,
        main_count=main_count,
        sub_count=sub_count,
    )


# 活动类型管理（高级管理）
@router.get("/admin/types", response_class=HTMLResponse)
def admin_event_types(request: Request, db = Depends(get_db), current_user = Depends(get_current_user)):
    require_admin(current_user)
    types = db.query(EventType).filter(EventType.is_deleted == False).order_by(EventType.updated_at.desc()).all()
    return render_template("admin_event_types.html", title="活动类型", current_user=current_user, types=types, msg=request.query_params.get("msg"))


@router.post("/admin/types/create")
def admin_create_event_type(name: str = Form(...), description: str = Form(""), db = Depends(get_db), current_user = Depends(get_current_user)):
    require_admin(current_user)
    et = EventType(name=name.strip(), description=description or "")
    db.add(et); db.commit()
    return RedirectResponse("/admin/types?msg=已创建", status_code=302)


@router.post("/admin/types/{type_id}/update")
def admin_update_event_type(type_id: int, name: str = Form(...), description: str = Form(""), db = Depends(get_db), current_user = Depends(get_current_user)):
    require_admin(current_user)
    et = db.get(EventType, type_id)
    if not et or et.is_deleted:
        raise HTTPException(404, "类型不存在")
    et.name = name.strip()
    et.description = description or ""
    et.updated_at = datetime.now(TZ)
    db.commit()
    return RedirectResponse("/admin/types?msg=已保存", status_code=302)


@router.post("/admin/types/{type_id}/toggle")
def admin_toggle_event_type(type_id: int, db = Depends(get_db), current_user = Depends(get_current_user)):
    require_admin(current_user)
    et = db.get(EventType, type_id)
    if not et or et.is_deleted:
        raise HTTPException(404, "类型不存在")
    et.is_active = not et.is_active
    et.updated_at = datetime.now(TZ)
    db.commit()
    return RedirectResponse("/admin/types?msg=已切换状态", status_code=302)


@router.post("/admin/types/{type_id}/delete")
def admin_delete_event_type(type_id: int, db = Depends(get_db), current_user = Depends(get_current_user)):
    require_admin(current_user)
    et = db.get(EventType, type_id)
    if not et:
        raise HTTPException(404, "类型不存在")
    et.is_deleted = True
    db.commit()
    return RedirectResponse("/admin/types?msg=已移入垃圾箱", status_code=302)


@router.get("/admin/review", response_class=HTMLResponse)
def admin_review_list(request: Request, db = Depends(get_db), current_user = Depends(get_current_user)):
    require_admin_or_reviewer(current_user)
    # filters
    event_id = request.query_params.get("event_id")
    q = (request.query_params.get("q") or "").strip()
    sub_q = db.query(Submission).filter(Submission.is_deleted == False)
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
            "username": s.user.username if s.user else "—",
            "event_name": s.event.name if s.event else "—",
            "pending": pending,
            "ok": ok,
            "rev": rev,
        })
    rows.sort(key=lambda r: r["created_at"], reverse=True)
    events = db.query(Event).filter(Event.is_deleted == False).order_by(Event.id.desc()).all()
    eid = int(event_id) if event_id and event_id.isdigit() else None
    return render_template("admin_review.html", title="审核中心", current_user=current_user, rows=rows, events=events, event_id=eid, q=q)


@router.get("/admin/review/{sub_id}", response_class=HTMLResponse)
def admin_review_detail(sub_id: int, request: Request, db = Depends(get_db), current_user = Depends(get_current_user)):
    require_admin_or_reviewer(current_user)
    sub = db.get(Submission, sub_id)
    if not sub or sub.is_deleted:
        raise HTTPException(404, "提交不存在")
    items = db.query(SubmissionItem).filter(SubmissionItem.submission_id == sub_id).all()
    return render_template("admin_review_detail.html", title="审核提交", current_user=current_user, sub=sub, user=sub.user, event=sub.event, items=items)


@router.post("/admin/review/{sub_id}/approve_all")
def admin_review_approve_all(sub_id: int, db = Depends(get_db), current_user = Depends(get_current_user)):
    require_admin_or_reviewer(current_user)
    items = db.query(SubmissionItem).filter(SubmissionItem.submission_id == sub_id).all()
    for it in items:
        if not it.approved:
            it.approved = True
    db.commit()
    return RedirectResponse(f"/admin/review/{sub_id}?msg=全部通过", status_code=302)


@router.post("/admin/review/event/{event_id}/approve_all")
def admin_review_approve_event_all(event_id: int, db = Depends(get_db), current_user = Depends(get_current_user)):
    require_admin_or_reviewer(current_user)
    items = (
        db.query(SubmissionItem)
        .join(Submission, Submission.id == SubmissionItem.submission_id)
        .filter(Submission.event_id == event_id, Submission.is_deleted == False, SubmissionItem.approved == False)
        .all()
    )
    for it in items:
        it.approved = True
    db.commit()
    return RedirectResponse(f"/admin/review?event_id={event_id}&msg=已通过该活动全部待审", status_code=302)


@router.post("/admin/review/item/{item_id}/toggle_approve")
def admin_toggle_approve(item_id: int, db = Depends(get_db), current_user = Depends(get_current_user)):
    require_admin_or_reviewer(current_user)
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
    require_admin_or_reviewer(current_user)
    it = db.get(SubmissionItem, item_id)
    if not it:
        raise HTTPException(404, "条目不存在")
    if it.approved:
        it.revoked = not it.revoked
    db.commit()
    return RedirectResponse(f"/admin/review/{it.submission_id}?msg=已切换撤销状态", status_code=302)


@router.post("/admin/review/{sub_id}/delete")
def admin_delete_submission(sub_id: int, db = Depends(get_db), current_user = Depends(get_current_user)):
    require_admin_or_reviewer(current_user)
    sub = db.get(Submission, sub_id)
    if not sub or sub.is_deleted:
        return RedirectResponse("/admin/review?msg=提交不存在或已删除", status_code=302)
    sub.is_deleted = True
    db.commit()
    return RedirectResponse("/admin/review?msg=提交已移入垃圾箱", status_code=302)


@router.get("/admin/events", response_class=HTMLResponse)
def admin_events(request: Request, db = Depends(get_db), current_user = Depends(get_current_user)):
    require_admin_or_reviewer(current_user)
    events = db.query(Event).filter(Event.is_deleted == False).all()
    types = db.query(EventType).filter(EventType.is_deleted == False, EventType.is_active == True).order_by(EventType.name.asc()).all()
    now = datetime.now(TZ)
    return render_template("admin_events.html", title="活动管理", current_user=current_user, events=events, year=now.year, month=now.month, types=types)


@router.post("/admin/events/create")
def admin_create_event(request: Request, name: str = Form(...), start: str = Form(""), end: str = Form(""), weight: float = Form(1.0), event_type_id: int = Form(None), is_reproduction: int = Form(0), allow_wp_only: int = Form(0), db = Depends(get_db), current_user = Depends(get_current_user)):
    require_admin_or_reviewer(current_user)

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
        allow_wp_only=bool(int(allow_wp_only)),
        event_type_id=int(event_type_id) if event_type_id else None,
    )
    db.add(evt)
    db.commit()
    return RedirectResponse("/admin/events?msg=已创建", status_code=302)


@router.get("/admin/events/{event_id}/edit", response_class=HTMLResponse)
def admin_edit_event(event_id: int, request: Request, db = Depends(get_db), current_user = Depends(get_current_user)):
    require_admin_or_reviewer(current_user)
    event = db.get(Event, event_id)
    if not event:
        raise HTTPException(404, "活动不存在")
    types = db.query(EventType).filter(EventType.is_deleted == False, EventType.is_active == True).order_by(EventType.name.asc()).all()
    return render_template("admin_event_edit.html", title="编辑活动", current_user=current_user, event=event, types=types)


@router.post("/admin/events/{event_id}/edit")
def admin_update_event(event_id: int, name: str = Form(...), start: str = Form(""), end: str = Form(""), weight: float = Form(1.0), event_type_id: int = Form(None), is_reproduction: int = Form(0), allow_wp_only: int = Form(0), db = Depends(get_db), current_user = Depends(get_current_user)):
    require_admin_or_reviewer(current_user)

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
    evt.allow_wp_only = bool(int(allow_wp_only))
    evt.event_type_id = int(event_type_id) if event_type_id else None
    db.commit()
    return RedirectResponse("/admin/events?msg=已保存", status_code=302)


@router.post("/admin/events/{event_id}/toggle_active")
def admin_toggle_event_active(event_id: int, db = Depends(get_db), current_user = Depends(get_current_user)):
    require_admin_or_reviewer(current_user)
    evt = db.get(Event, event_id)
    if not evt:
        raise HTTPException(404, "活动不存在")
    evt.is_active = not evt.is_active
    db.commit()
    return RedirectResponse("/admin/events?msg=已切换状态", status_code=302)


@router.post("/admin/events/{event_id}/delete")
def admin_delete_event(event_id: int, db = Depends(get_db), current_user = Depends(get_current_user)):
    require_admin_or_reviewer(current_user)
    evt = db.get(Event, event_id)
    if not evt:
        raise HTTPException(404, "活动不存在")
    # 软删除：标记后进入垃圾箱
    evt.is_deleted = True
    db.commit()
    return RedirectResponse(f"/admin/events?msg=已移入垃圾箱", status_code=302)


@router.get("/admin/events/{event_id}", response_class=HTMLResponse)
def admin_event_detail(event_id: int, request: Request, db = Depends(get_db), current_user = Depends(get_current_user)):
    require_admin_or_reviewer(current_user)
    event = db.get(Event, event_id)
    if not event:
        raise HTTPException(404, "活动不存在")
    users = db.query(User).filter(User.is_deleted == False).order_by(User.username.asc()).all()
    rows = []
    for u in users:
        subs = db.query(Submission).filter(Submission.user_id == u.id, Submission.event_id == event_id, Submission.is_deleted == False).all()
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
    require_admin_or_reviewer(current_user)
    event = db.get(Event, event_id)
    if not event:
        raise HTTPException(404, "活动不存在")
    q = (request.query_params.get("q") or "").strip()
    cat = request.query_params.get("cat") or ""
    ch_q = db.query(Challenge).filter(Challenge.event_id == event_id, Challenge.is_deleted == False)
    if cat:
        ch_q = ch_q.filter(Challenge.category == cat)
    if q:
        ch_q = ch_q.filter(Challenge.name.contains(q))
    challenges = ch_q.all()
    return render_template("admin_challenges.html", title="题目管理", current_user=current_user, event=event, challenges=challenges, categories=CATEGORIES, msg=request.query_params.get("msg"), q=q, cat=cat)


@router.post("/admin/events/{event_id}/challenges/add")
def admin_add_challenge(event_id: int, name: str = Form(...), category: str = Form("misc"), base_score: int = Form(100), db = Depends(get_db), current_user = Depends(get_current_user)):
    require_admin_or_reviewer(current_user)
    event = db.get(Event, event_id)
    if not event:
        raise HTTPException(404, "活动不存在")
    ch = Challenge(event_id=event_id, name=name.strip(), category=category.strip(), base_score=int(base_score))
    db.add(ch)
    db.commit()
    return RedirectResponse(f"/admin/events/{event_id}/challenges?msg=已添加", status_code=302)


@router.post("/admin/events/{event_id}/challenges/{ch_id}/delete")
def admin_delete_challenge(event_id: int, ch_id: int, db = Depends(get_db), current_user = Depends(get_current_user)):
    require_admin_or_reviewer(current_user)
    event = db.get(Event, event_id)
    if not event:
        raise HTTPException(404, "活动不存在")
    ch = db.get(Challenge, ch_id)
    if not ch or ch.event_id != event_id:
        raise HTTPException(404, "题目不存在")

    refcount = db.query(SubmissionItem).filter(SubmissionItem.challenge_id == ch_id).count()
    # 统一软删除（即使被引用也仅从列表隐藏）
    ch.is_deleted = True
    db.commit()
    if refcount > 0:
        return RedirectResponse(f"/admin/events/{event_id}/challenges?msg=已移入垃圾箱(仍被{refcount}条提交引用)", status_code=302)
    return RedirectResponse(f"/admin/events/{event_id}/challenges?msg=已移入垃圾箱", status_code=302)


@router.post("/admin/events/{event_id}/challenges/{ch_id}/update")
def admin_update_challenge(event_id: int, ch_id: int, base_score: int = Form(...), db = Depends(get_db), current_user = Depends(get_current_user)):
    require_admin_or_reviewer(current_user)
    ch = db.get(Challenge, ch_id)
    if not ch or ch.event_id != event_id:
        raise HTTPException(404, "题目不存在")
    ch.base_score = int(base_score)
    db.commit()
    return RedirectResponse(f"/admin/events/{event_id}/challenges?msg=分值已更新", status_code=302)


@router.post("/admin/review/{sub_id}/set_points")
def admin_set_submission_points(sub_id: int, manual_points: float = Form(None), clear: int = Form(0), db = Depends(get_db), current_user = Depends(get_current_user)):
    require_admin_or_reviewer(current_user)
    sub = db.get(Submission, sub_id)
    if not sub:
        raise HTTPException(404, "提交不存在")
    if clear and int(clear) == 1:
        sub.manual_points = None
    else:
        try:
            sub.manual_points = float(manual_points)
        except Exception:
            sub.manual_points = None
    db.commit()
    return RedirectResponse(f"/admin/review/{sub_id}?msg=分数已更新", status_code=302)


@router.post("/admin/users/{uid}/avatar/clear")
def admin_clear_user_avatar(uid: int, db = Depends(get_db), current_user = Depends(get_current_user)):
    require_admin(current_user)
    u = db.get(User, uid)
    if not u or u.is_deleted:
        raise HTTPException(404, "用户不存在")
    # 删除磁盘上的头像文件
    from pathlib import Path
    from ..config import IMAGE_DIR
    import os
    old_name = (u.avatar_filename or '').strip()
    if old_name:
        try:
            os.remove(Path(IMAGE_DIR) / old_name)
        except Exception:
            pass
    u.avatar_filename = None
    db.commit()
    return RedirectResponse(f"/admin/users/{uid}?msg=头像已清除", status_code=302)


# 公告管理
@router.get("/admin/announcements", response_class=HTMLResponse)
def admin_announcements(request: Request, db = Depends(get_db), current_user = Depends(get_current_user)):
    require_admin(current_user)
    anns = db.query(Announcement).filter(Announcement.is_deleted == False).order_by(Announcement.updated_at.desc()).all()
    return render_template("admin_announcements.html", title="公告管理", current_user=current_user, anns=anns, msg=request.query_params.get("msg"))


@router.post("/admin/announcements/create")
def admin_create_announcement(title: str = Form(...), content: str = Form(""), visible: int = Form(1), db = Depends(get_db), current_user = Depends(get_current_user)):
    require_admin(current_user)
    ann = Announcement(title=title.strip(), content=content or "", visible=bool(int(visible)))
    db.add(ann); db.commit()
    return RedirectResponse("/admin/announcements?msg=已创建", status_code=302)


@router.post("/admin/announcements/{ann_id}/update")
def admin_update_announcement(ann_id: int, title: str = Form(...), content: str = Form(""), visible: int = Form(1), db = Depends(get_db), current_user = Depends(get_current_user)):
    require_admin(current_user)
    ann = db.get(Announcement, ann_id)
    if not ann or ann.is_deleted:
        raise HTTPException(404, "公告不存在")
    ann.title = title.strip()
    ann.content = content or ""
    ann.visible = bool(int(visible))
    ann.updated_at = datetime.now(TZ)
    db.commit()
    return RedirectResponse("/admin/announcements?msg=已保存", status_code=302)


@router.post("/admin/announcements/{ann_id}/toggle")
def admin_toggle_announcement(ann_id: int, db = Depends(get_db), current_user = Depends(get_current_user)):
    require_admin(current_user)
    ann = db.get(Announcement, ann_id)
    if not ann or ann.is_deleted:
        raise HTTPException(404, "公告不存在")
    ann.visible = not ann.visible
    ann.updated_at = datetime.now(TZ)
    db.commit()
    return RedirectResponse("/admin/announcements?msg=已切换可见性", status_code=302)


@router.post("/admin/announcements/{ann_id}/delete")
def admin_delete_announcement(ann_id: int, db = Depends(get_db), current_user = Depends(get_current_user)):
    require_admin(current_user)
    ann = db.get(Announcement, ann_id)
    if not ann:
        raise HTTPException(404, "公告不存在")
    ann.is_deleted = True
    db.commit()
    return RedirectResponse("/admin/announcements?msg=已移入垃圾箱", status_code=302)


# 规则编辑
@router.get("/admin/rules", response_class=HTMLResponse)
def admin_rules_page(request: Request, db = Depends(get_db), current_user = Depends(get_current_user)):
    require_admin(current_user)
    s = db.get(Setting, 'rules_md')
    rules_md = s.value if s else ''
    return render_template("admin_rules.html", title="规则编辑", current_user=current_user, rules_md=rules_md)


@router.post("/admin/rules")
def admin_rules_save(rules_md: str = Form(""), db = Depends(get_db), current_user = Depends(get_current_user)):
    require_admin(current_user)
    s = db.get(Setting, 'rules_md')
    if not s:
        s = Setting(key='rules_md', value=rules_md or '')
        db.add(s)
    else:
        s.value = rules_md or ''
    db.commit()
    return RedirectResponse("/admin/rules?msg=已保存", status_code=302)


# 垃圾箱
@router.get("/admin/trash", response_class=HTMLResponse)
def admin_trash(request: Request, db = Depends(get_db), current_user = Depends(get_current_user)):
    require_admin(current_user)
    trashed_events = db.query(Event).filter(Event.is_deleted == True).order_by(Event.id.desc()).all()
    trashed_challenges = db.query(Challenge).filter(Challenge.is_deleted == True).order_by(Challenge.id.desc()).all()
    trashed_anns = db.query(Announcement).filter(Announcement.is_deleted == True).order_by(Announcement.id.desc()).all()
    trashed_subs = db.query(Submission).filter(Submission.is_deleted == True).order_by(Submission.id.desc()).all()
    trashed_users = db.query(User).filter(User.is_deleted == True).order_by(User.username.asc()).all()
    trashed_adjs = db.query(PointAdjustment).filter(PointAdjustment.is_deleted == True).order_by(PointAdjustment.created_at.desc()).all()
    # build username mapping for adjustments
    user_ids = list({a.user_id for a in trashed_adjs})
    users_map = {u.id: u.username for u in db.query(User).filter(User.id.in_(user_ids)).all()} if user_ids else {}
    return render_template(
        "admin_trash.html",
        title="垃圾箱",
        current_user=current_user,
        trashed_events=trashed_events,
        trashed_challenges=trashed_challenges,
        trashed_anns=trashed_anns,
        trashed_subs=trashed_subs,
        trashed_users=trashed_users,
        trashed_adjs=trashed_adjs,
        users_map=users_map,
    )

@router.post("/admin/trash/adjustment/{adj_id}/restore")
def trash_restore_adjustment(adj_id: int, db = Depends(get_db), current_user = Depends(get_current_user)):
    require_admin(current_user)
    adj = db.get(PointAdjustment, adj_id)
    if not adj:
        raise HTTPException(404, "调整不存在")
    adj.is_deleted = False
    db.commit()
    return RedirectResponse("/admin/trash?msg=已恢复积分调整", status_code=302)

@router.post("/admin/trash/adjustment/{adj_id}/purge")
def trash_purge_adjustment(adj_id: int, db = Depends(get_db), current_user = Depends(get_current_user)):
    require_admin(current_user)
    adj = db.get(PointAdjustment, adj_id)
    if not adj:
        raise HTTPException(404, "调整不存在")
    db.delete(adj)
    db.commit()
    return RedirectResponse("/admin/trash?msg=已彻底删除积分调整", status_code=302)


@router.post("/admin/trash/event/{event_id}/restore")
def trash_restore_event(event_id: int, db = Depends(get_db), current_user = Depends(get_current_user)):
    require_admin(current_user)
    evt = db.get(Event, event_id)
    if not evt:
        raise HTTPException(404, "活动不存在")
    evt.is_deleted = False
    db.commit()
    return RedirectResponse("/admin/trash?msg=已恢复活动", status_code=302)


@router.post("/admin/trash/event/{event_id}/purge")
def trash_purge_event(event_id: int, db = Depends(get_db), current_user = Depends(get_current_user)):
    require_admin(current_user)
    evt = db.get(Event, event_id)
    if not evt:
        raise HTTPException(404, "活动不存在")
    # 删除关联提交与题目后，删除活动
    items = db.query(SubmissionItem).join(Submission, Submission.id == SubmissionItem.submission_id).filter(Submission.event_id == event_id).all()
    for it in items:
        db.delete(it)
    subs = db.query(Submission).filter(Submission.event_id == event_id).all()
    for s in subs:
        db.delete(s)
    chs = db.query(Challenge).filter(Challenge.event_id == event_id).all()
    for ch in chs:
        db.delete(ch)
    db.delete(evt)
    db.commit()
    return RedirectResponse("/admin/trash?msg=已彻底删除活动", status_code=302)


@router.post("/admin/trash/challenge/{ch_id}/restore")
def trash_restore_challenge(ch_id: int, db = Depends(get_db), current_user = Depends(get_current_user)):
    require_admin(current_user)
    ch = db.get(Challenge, ch_id)
    if not ch:
        raise HTTPException(404, "题目不存在")
    ch.is_deleted = False
    db.commit()
    return RedirectResponse("/admin/trash?msg=已恢复题目", status_code=302)


@router.post("/admin/trash/challenge/{ch_id}/purge")
def trash_purge_challenge(ch_id: int, db = Depends(get_db), current_user = Depends(get_current_user)):
    require_admin(current_user)
    ch = db.get(Challenge, ch_id)
    if not ch:
        raise HTTPException(404, "题目不存在")
    db.delete(ch)
    db.commit()
    return RedirectResponse("/admin/trash?msg=已彻底删除题目", status_code=302)


@router.post("/admin/trash/announcement/{ann_id}/restore")
def trash_restore_announcement(ann_id: int, db = Depends(get_db), current_user = Depends(get_current_user)):
    require_admin(current_user)
    ann = db.get(Announcement, ann_id)
    if not ann:
        raise HTTPException(404, "公告不存在")
    ann.is_deleted = False
    db.commit()
    return RedirectResponse("/admin/trash?msg=已恢复公告", status_code=302)


@router.post("/admin/trash/announcement/{ann_id}/purge")
def trash_purge_announcement(ann_id: int, db = Depends(get_db), current_user = Depends(get_current_user)):
    require_admin(current_user)
    ann = db.get(Announcement, ann_id)
    if not ann:
        raise HTTPException(404, "公告不存在")
    db.delete(ann)
    db.commit()
    return RedirectResponse("/admin/trash?msg=已彻底删除公告", status_code=302)


@router.post("/admin/trash/submission/{sub_id}/restore")
def trash_restore_submission(sub_id: int, db = Depends(get_db), current_user = Depends(get_current_user)):
    require_admin(current_user)
    sub = db.get(Submission, sub_id)
    if not sub:
        raise HTTPException(404, "提交不存在")
    sub.is_deleted = False
    db.commit()
    return RedirectResponse("/admin/trash?msg=已恢复提交", status_code=302)


@router.post("/admin/trash/submission/{sub_id}/purge")
def trash_purge_submission(sub_id: int, db = Depends(get_db), current_user = Depends(get_current_user)):
    require_admin(current_user)
    sub = db.get(Submission, sub_id)
    if not sub:
        raise HTTPException(404, "提交不存在")
    # 删除关联的 items 再删提交
    items = db.query(SubmissionItem).filter(SubmissionItem.submission_id == sub_id).all()
    for it in items:
        db.delete(it)
    db.delete(sub)
    db.commit()
    return RedirectResponse("/admin/trash?msg=已彻底删除提交", status_code=302)


# 手工积分调整（仅限当月生效，不跨月）
@router.get("/admin/adjustments", response_class=HTMLResponse)
def admin_adjustments(request: Request, db = Depends(get_db), current_user = Depends(get_current_user)):
    require_admin(current_user)
    now = datetime.now(TZ)
    year = int(request.query_params.get("year") or now.year)
    month = int(request.query_params.get("month") or now.month)
    # 仅显示未删除成员
    users = db.query(User).filter(User.is_deleted == False).order_by(User.username.asc()).all()
    user_map = {u.id: u.username for u in users}
    adjs = (
        db.query(PointAdjustment)
        .filter(PointAdjustment.is_deleted == False, PointAdjustment.year == year, PointAdjustment.month == month)
        .order_by(PointAdjustment.created_at.desc())
        .all()
    )
    return render_template("admin_adjustments.html", title="积分调整", current_user=current_user, users=users, adjs=adjs, year=year, month=month, user_map=user_map)


@router.post("/admin/adjustments/create")
def admin_create_adjustment(user_id: int = Form(...), amount: float = Form(...), reason: str = Form(""), year: int = Form(...), month: int = Form(...), db = Depends(get_db), current_user = Depends(get_current_user)):
    require_admin(current_user)
    u = db.get(User, int(user_id))
    if not u or u.is_deleted:
        raise HTTPException(404, "用户不存在")
    adj = PointAdjustment(user_id=u.id, amount=float(amount), reason=reason or "", year=int(year), month=int(month), created_by_id=current_user.id)
    db.add(adj); db.commit()
    return RedirectResponse(f"/admin/adjustments?year={year}&month={month}&msg=已添加", status_code=302)


@router.post("/admin/adjustments/{adj_id}/delete")
def admin_delete_adjustment(adj_id: int, db = Depends(get_db), current_user = Depends(get_current_user)):
    require_admin(current_user)
    adj = db.get(PointAdjustment, adj_id)
    if not adj:
        raise HTTPException(404, "记录不存在")
    adj.is_deleted = True
    db.commit()
    return RedirectResponse(f"/admin/adjustments?year={adj.year}&month={adj.month}&msg=已移入垃圾箱", status_code=302)


@router.get("/admin/users", response_class=HTMLResponse)
def admin_users(request: Request, db = Depends(get_db), current_user = Depends(get_current_user)):
    # 审核员可浏览成员列表（只读），管理员可编辑
    require_admin_or_reviewer(current_user)
    users = db.query(User).filter(User.is_deleted == False).order_by(User.username.asc()).all()
    return render_template("admin_users.html", title="成员管理", current_user=current_user, users=users)


@router.get("/admin/users/{uid}", response_class=HTMLResponse)
def admin_user_detail(uid: int, request: Request, db = Depends(get_db), current_user = Depends(get_current_user)):
    # 审核员可浏览成员详情（只读），管理员可编辑
    require_admin_or_reviewer(current_user)
    u = db.get(User, uid)
    if not u or u.is_deleted:
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
    if not u or u.is_deleted:
        raise HTTPException(404, "用户不存在")
    u.password_hash = pwdhash.hash(new_password)
    db.add(u); db.commit()
    return RedirectResponse(f"/admin/users/{uid}?msg=密码已更新", status_code=302)


@router.post("/admin/users/{uid}/avatar")
async def admin_set_user_avatar(uid: int, file: UploadFile = File(...), db = Depends(get_db), current_user = Depends(get_current_user)):
    from pathlib import Path
    from ..config import MAX_AVATAR_SIZE, IMAGE_DIR
    import os, uuid
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
    # 若用户已有头像文件，先尝试删除，避免遗留
    old_name = (u.avatar_filename or '').strip()
    if old_name:
        try:
            os.remove(Path(IMAGE_DIR) / old_name)
        except Exception:
            pass
    # 随机化文件名，避免与 user 绑定
    ext = ext_map[content_type]
    safe_name = f"av_{uuid.uuid4().hex}{ext}"
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
    if not u or u.is_deleted:
        raise HTTPException(404, "用户不存在")
    u.role = role if role in ("member", "reviewer", "admin") else u.role
    u.team_type = team_type if team_type in ("main", "sub") else u.team_type
    db.commit()
    return RedirectResponse("/admin/users?msg=已更新", status_code=302)


@router.post("/admin/users/{uid}/delete")
def admin_delete_user(uid: int, db = Depends(get_db), current_user = Depends(get_current_user)):
    require_admin(current_user)
    u = db.get(User, uid)
    if not u or u.is_deleted:
        return RedirectResponse("/admin/users?msg=用户不存在或已在垃圾箱", status_code=302)
    u.is_deleted = True
    db.commit()
    return RedirectResponse("/admin/users?msg=已移入垃圾箱", status_code=302)


@router.post("/admin/trash/user/{uid}/restore")
def trash_restore_user(uid: int, db = Depends(get_db), current_user = Depends(get_current_user)):
    require_admin(current_user)
    u = db.get(User, uid)
    if not u:
        raise HTTPException(404, "用户不存在")
    u.is_deleted = False
    db.commit()
    return RedirectResponse("/admin/trash?msg=已恢复成员", status_code=302)


@router.post("/admin/trash/user/{uid}/purge")
def trash_purge_user(uid: int, db = Depends(get_db), current_user = Depends(get_current_user)):
    require_admin(current_user)
    u = db.get(User, uid)
    if not u:
        raise HTTPException(404, "用户不存在")
    # 删除头像文件
    from pathlib import Path
    from ..config import IMAGE_DIR
    import os
    try:
        if u.avatar_filename:
            os.remove(Path(IMAGE_DIR) / u.avatar_filename)
    except Exception:
        pass
    # 解除外键依赖并删除相关数据
    # 1) 删除该用户的提交及条目
    subs = db.query(Submission).filter(Submission.user_id == uid).all()
    for s in subs:
        items = db.query(SubmissionItem).filter(SubmissionItem.submission_id == s.id).all()
        for it in items:
            db.delete(it)
        db.delete(s)
    # 2) 删除该用户作为被调整者的积分调整；将其作为创建者的记录 creator 置空
    adjs_user = db.query(PointAdjustment).filter(PointAdjustment.user_id == uid).all()
    for a in adjs_user:
        db.delete(a)
    adjs_created = db.query(PointAdjustment).filter(PointAdjustment.created_by_id == uid).all()
    for a in adjs_created:
        a.created_by_id = None
    # 3) 最后删除用户
    db.delete(u)
    db.commit()
    return RedirectResponse("/admin/trash?msg=已彻底删除成员", status_code=302)


# 按月清空提交功能已移除：保留历史数据，月榜通过时间范围统计实现
