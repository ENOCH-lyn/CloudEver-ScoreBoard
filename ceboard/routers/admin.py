from datetime import datetime

from fastapi import APIRouter, Depends, Form, HTTPException, Request, UploadFile, File, BackgroundTasks
from fastapi.responses import RedirectResponse, HTMLResponse

from ..deps import get_db, get_current_user, require_admin, render_template, require_admin_or_reviewer
from ..models import Event, Challenge, Submission, SubmissionItem, User, Announcement, PointAdjustment, EventType, Setting
from ..models import Setting
from ..models import Notification
import uuid
from ..config import TZ, CATEGORIES
from ..utils import compute_submission_points, now_tokyo, send_email_sync


router = APIRouter()

# 后台发送邮件（避免阻塞请求）
def _bg_send_email(to_addr: str, subject: str, content: str):
    try:
        from ..database import SessionLocal
        with SessionLocal() as s:
            try:
                send_email_sync(s, to_addr, subject, content)
            except Exception:
                pass
    except Exception:
        pass
@router.get("/admin/advanced", response_class=HTMLResponse)
def admin_advanced_dashboard(request: Request, db = Depends(get_db), current_user = Depends(get_current_user)):
    require_admin(current_user)
    # Simple KPIs
    active_events = db.query(Event).filter(Event.is_active == True, Event.is_deleted == False).count()
    recent_subs = db.query(Submission).filter(Submission.is_deleted == False).order_by(Submission.created_at.desc()).limit(5).all()
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


@router.get("/admin/notifications", response_class=HTMLResponse)
def admin_notifications_page(request: Request, page: int = 1, db = Depends(get_db), current_user = Depends(get_current_user)):
    """分组显示通知：同一 batch_id 合并，直接展示已读/未读用户名列表。"""
    require_admin(current_user)
    q = (request.query_params.get("q") or "").strip()
    user_id = request.query_params.get("user_id")
    base_q = db.query(Notification).filter(Notification.is_deleted == False)
    if q:
        base_q = base_q.filter(Notification.content.contains(q))
    # 先取所有（用于分组统计再分页）
    all_notifs = base_q.order_by(Notification.created_at.desc()).all()
    groups = {}
    for n in all_notifs:
        # user filter：只保留包含该用户的分组（统计该用户时仍展示整个分组，便于批量操作）
        if user_id and user_id.isdigit() and n.user_id != int(user_id):
            continue
        bid = n.batch_id or f"single-{n.id}"
        g = groups.get(bid)
        if not g:
            groups[bid] = {
                'batch_id': bid,
                'title': n.title or '通知',
                'created_at': n.created_at,
                'content': n.content,
                'type': n.type,
                'items': [],
                'read_count': 0,
                'total_count': 0,
            }
            g = groups[bid]
        g['items'].append(n)
    # 补充统计与用户名
    rows = []
    for bid, g in groups.items():
        g['total_count'] = len(g['items'])
        g['read_count'] = sum(1 for it in g['items'] if it.read_at is not None)
        # 已读/未读用户名（全部显示，交给前端换行显示）
        read_users, unread_users = [], []
        for it in g['items']:
            u = db.get(User, it.user_id)
            name = (u.username if u else f"uid:{it.user_id}")
            (read_users if it.read_at else unread_users).append(name)
        rows.append({
            'batch_id': bid,
            'title': g['title'],
            'created_at': g['created_at'],
            'read_count': g['read_count'],
            'total_count': g['total_count'],
            'read_users': read_users,
            'unread_users': unread_users,
            'content': g['content'],
            'type': g['type'],
        })
    # 按创建时间排序
    rows.sort(key=lambda r: r['created_at'], reverse=True)
    # 分页（对分组后的 rows）
    page = max(1, int(page or 1))
    page_size = 10
    total = len(rows)
    paged = rows[(page-1)*page_size: page*page_size]
    users = db.query(User).filter(User.is_deleted == False).all()
    total_pages = (total + 10 - 1) // 10
    return render_template(
        "admin_notifications.html",
        title="通知管理",
        current_user=current_user,
        rows=paged,
        users=users,
        q=q,
        user_id=int(user_id) if user_id and user_id.isdigit() else None,
        page=page,
        total=total,
        total_pages=total_pages,
    )


## ---- duplicate create routes (later copy) removed ----


@router.get("/admin/notifications/create", response_class=HTMLResponse)
def admin_notifications_create_page(request: Request, db = Depends(get_db), current_user = Depends(get_current_user)):
    """发布通知页面：需要位于动态分组路由之前，防止被 {batch_id} 捕获。"""
    require_admin(current_user)
    users = db.query(User).filter(User.is_deleted == False, User.is_active == True).order_by(User.username.asc()).all()
    return render_template(
        "admin_notifications_create.html",
        title="发布通知",
        current_user=current_user,
        users=users,
        msg=request.query_params.get("msg"),
    )

@router.post("/admin/notifications/create")
def admin_notifications_create(title: str = Form(...), content: str = Form(...), user_ids: str = Form(""), send_email: int = Form(0), background_tasks: BackgroundTasks = None, db = Depends(get_db), current_user = Depends(get_current_user)):
    """发布通知：若未选择具体成员则广播。使用 batch_id 进行分组。"""
    require_admin(current_user)
    title_clean = (title or '').strip()
    text = (content or '').strip()
    if not title_clean or not text:
        return RedirectResponse("/admin/notifications/create?msg=标题和内容不能为空", status_code=302)
    all_users = db.query(User).filter(User.is_deleted == False, User.is_active == True).all()
    ids = [int(i) for i in (user_ids or '').split(',') if i.strip().isdigit()]
    target_users = all_users if not ids else [u for u in all_users if u.id in ids]
    if ids and not target_users:
        return RedirectResponse("/admin/notifications/create?msg=成员选择无效", status_code=302)
    batch_id = f"b{int(datetime.now(TZ).timestamp())}_{uuid.uuid4().hex[:8]}"
    for u in target_users:
        db.add(Notification(user_id=u.id, type='system', title=title_clean, content=text, batch_id=batch_id))
        if send_email and u.email:
            if background_tasks is not None:
                background_tasks.add_task(_bg_send_email, u.email, title_clean, text)
            else:
                # 没有 FastAPI 的 BackgroundTasks 注入时，使用线程后台发送，避免阻塞请求
                try:
                    import threading
                    threading.Thread(target=_bg_send_email, args=(u.email, title_clean, text), daemon=True).start()
                except Exception:
                    pass
    db.commit()
    return RedirectResponse(f"/admin/notifications?msg=已发布{len(target_users)}条" + ("(含邮件)" if send_email else ""), status_code=302)

@router.get("/admin/notifications/{batch_id}", response_class=HTMLResponse)
def admin_notifications_detail(batch_id: str, request: Request = None, db = Depends(get_db), current_user = Depends(get_current_user)):
    """查看某个通知分组的阅读明细。"""
    require_admin(current_user)
    if batch_id == 'create':  # 防止与创建路径冲突
        raise HTTPException(404, "无效通知标识")
    notifs = db.query(Notification).filter(Notification.batch_id == batch_id, Notification.is_deleted == False).order_by(Notification.created_at.asc()).all()
    # 兼容单条通知（无 batch_id），形如 single-<id>
    if not notifs and batch_id.startswith("single-"):
        try:
            nid = int(batch_id.split("-", 1)[1])
        except Exception:
            nid = None
        if nid:
            one = db.get(Notification, nid)
            if one and not one.is_deleted:
                notifs = [one]
    if not notifs:
        raise HTTPException(404, "通知分组不存在")
    title = notifs[0].title or "通知"
    content = notifs[0].content
    read_users, unread_users = [], []
    for n in notifs:
        u = db.get(User, n.user_id)
        name = (u.username if u else f"uid:{n.user_id}")
        (read_users if n.read_at else unread_users).append(name)
    return render_template(
        "admin_notifications_detail.html",
        title=f"通知阅读明细",
        current_user=current_user,
        batch_id=batch_id,
        notif_title=title,
        notif_content=content,
        read_users=read_users,
        unread_users=unread_users,
        total=len(notifs),
        read=len(read_users)
    )


@router.get("/admin/notifications/{batch_id}/edit", response_class=HTMLResponse)
def admin_notifications_edit_page(batch_id: str, request: Request = None, db = Depends(get_db), current_user = Depends(get_current_user)):
    require_admin(current_user)
    if batch_id == 'create':
        raise HTTPException(404, "无效通知标识")
    notifs = db.query(Notification).filter(Notification.batch_id == batch_id, Notification.is_deleted == False).order_by(Notification.created_at.desc()).all()
    # 兼容 single-<id>
    single_mode = False
    if not notifs and batch_id.startswith("single-"):
        try:
            nid = int(batch_id.split("-", 1)[1])
        except Exception:
            nid = None
        if nid:
            one = db.get(Notification, nid)
            if one and not one.is_deleted:
                notifs = [one]
                single_mode = True
    if not notifs:
        raise HTTPException(404, "通知分组不存在")
    # 收件人用户名列表
    usernames = []
    for n in notifs:
        u = db.get(User, n.user_id)
        if u: usernames.append(u.username)
    users = db.query(User).filter(User.is_deleted == False, User.is_active == True).order_by(User.username.asc()).all()
    return render_template(
        "admin_notifications_edit.html",
        title="编辑通知",
        current_user=current_user,
        batch_id=batch_id,
        users=users,
        usernames=usernames,
        title_value=(notifs[0].title or ""),
        content_value=(notifs[0].content or "")
    )




@router.post("/admin/notifications/{batch_id}/edit")
def admin_notifications_edit(batch_id: str, title: str = Form(""), content: str = Form(...), db = Depends(get_db), current_user = Depends(get_current_user)):
    """批量编辑：修改同一 batch 的全部通知内容与标题保持不变。"""
    require_admin(current_user)
    if batch_id == 'create':
        raise HTTPException(404, "无效通知标识")
    title_clean = (title or '').strip()
    text = (content or '').strip()
    if not text:
        return RedirectResponse("/admin/notifications?msg=内容不能为空", status_code=302)
    notifs = db.query(Notification).filter(Notification.batch_id == batch_id, Notification.is_deleted == False).all()
    if not notifs and batch_id.startswith("single-"):
        try:
            nid = int(batch_id.split("-", 1)[1])
        except Exception:
            nid = None
        if nid:
            one = db.get(Notification, nid)
            if one and not one.is_deleted:
                notifs = [one]
    if not notifs:
        raise HTTPException(404, "分组不存在")
    for n in notifs:
        if title_clean:
            n.title = title_clean
        n.content = text
    db.commit()
    return RedirectResponse("/admin/notifications?msg=已批量保存", status_code=302)


@router.post("/admin/notifications/{batch_id}/delete")
def admin_notifications_delete(batch_id: str, db = Depends(get_db), current_user = Depends(get_current_user)):
    """批量删除：软删除进入垃圾箱。"""
    require_admin(current_user)
    if batch_id == 'create':
        return RedirectResponse("/admin/notifications?msg=无效通知标识", status_code=302)
    notifs = db.query(Notification).filter(Notification.batch_id == batch_id, Notification.is_deleted == False).all()
    if not notifs and batch_id.startswith("single-"):
        try:
            nid = int(batch_id.split("-", 1)[1])
        except Exception:
            nid = None
        if nid:
            one = db.get(Notification, nid)
            if one and not one.is_deleted:
                notifs = [one]
    if not notifs:
        return RedirectResponse("/admin/notifications?msg=分组不存在或已删除", status_code=302)
    for n in notifs:
        n.is_deleted = True
    db.commit()
    return RedirectResponse("/admin/notifications?msg=已批量删除", status_code=302)


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
def admin_review_list(request: Request, page: int = 1, db = Depends(get_db), current_user = Depends(get_current_user)):
    require_admin_or_reviewer(current_user)
    # filters
    event_id = request.query_params.get("event_id")
    q = (request.query_params.get("q") or "").strip()
    status = (request.query_params.get("status") or "unreviewed").strip()  # default 显示未审核
    base_q = db.query(Submission).filter(Submission.is_deleted == False)
    if event_id and event_id.isdigit():
        base_q = base_q.filter(Submission.event_id == int(event_id))
    # 先按时间排序取全量（便于基于“审核状态”的过滤），再进行内存分页
    subs_all = base_q.order_by(Submission.created_at.desc()).all()
    rows_all = []
    for s in subs_all:
        if q and s.user and (q.lower() not in s.user.username.lower()):
            continue
        total_items = len(s.items)
        pending = sum(1 for it in s.items if not it.approved)
        ok = sum(1 for it in s.items if it.approved and not it.revoked)
        rev = sum(1 for it in s.items if it.revoked)
        manual_set = (getattr(s, 'manual_points', None) is not None)
        # 已审核判定：
        # - 若存在条目，则“无待审”即视为已审核（无论通过或撤销都算处理过）；
        # - 若不存在条目（活动没有题目等），只有设置了手动分数才视为已审核；否则为未审核。
        # - 业务变更：被驳回的提交也视为“已审核”；当成员重新编辑后会清除驳回标记并重新进入“未审核”。
        is_reviewed = getattr(s, 'rejected', False) or (total_items > 0 and pending == 0) or (total_items == 0 and manual_set)
        # 分数：仅非驳回且视为“已审核”的显示分数，否则为 None
        pts = compute_submission_points(s) if (is_reviewed and not getattr(s, 'rejected', False)) else None
        rows_all.append({
            "sub_id": s.id,
            "created_at": s.created_at,
            "username": s.user.username if s.user else "—",
            "event_name": s.event.name if s.event else "—",
            "pending": pending,
            "ok": ok,
            "rev": rev,
            "rejected": getattr(s, 'rejected', False),
            "reviewed": is_reviewed,
            "points": pts,
        })
    # 基于审核状态过滤
    if status == 'reviewed':
        rows_filtered = [r for r in rows_all if r['reviewed']]
    elif status == 'unreviewed':
        rows_filtered = [r for r in rows_all if not r['reviewed']]
    elif status == 'all':
        rows_filtered = rows_all
    else:
        # 兜底：未知状态按未审核处理
        rows_filtered = [r for r in rows_all if not r['reviewed']]
    # 排序与分页
    rows_filtered.sort(key=lambda r: r["created_at"], reverse=True)
    page_size = 10
    page = max(1, int(page or 1))
    total_subs = len(rows_filtered)
    total_pages = (total_subs + page_size - 1) // page_size
    start = (page - 1) * page_size
    end = start + page_size
    rows = rows_filtered[start:end]
    # 事件选择下拉按照与“活动管理”相同的优先级排序
    events = db.query(Event).filter(Event.is_deleted == False).all()
    now = datetime.now(TZ)
    def _aware(dt):
        if not dt:
            return None
        try:
            if dt.tzinfo is None:
                return dt.replace(tzinfo=TZ)
            return dt.astimezone(TZ)
        except Exception:
            return dt

    def sort_key(e: Event):
        has_range = 1 if (e.start_time and e.end_time) else 0
        if has_range:
            s = _aware(e.start_time)
            ed = _aware(e.end_time)
            if s and ed and s <= now and now < ed:
                group = 0
                dist = (now - s).total_seconds() if s else 0
            else:
                group = 1
                if ed and ed < now:
                    dist = (now - ed).total_seconds()
                else:
                    dist = (s - now).total_seconds() if s else 0
        else:
            group = 2
            dist = float('inf')
        return (-has_range, group, dist, e.id * -1)

    events.sort(key=sort_key)
    eid = int(event_id) if event_id and event_id.isdigit() else None
    return render_template("admin_review.html", title="审核中心", current_user=current_user, rows=rows, events=events, event_id=eid, q=q, status=status, page=page, total_pages=total_pages, total=total_subs)


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
    sub = db.get(Submission, sub_id)
    if sub and getattr(sub, 'rejected', False):
        return RedirectResponse(f"/admin/review/{sub_id}?msg=该提交已被驳回，需成员重新提交后才能通过", status_code=302)
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
    # 驳回状态下禁止操作
    if it.submission and getattr(it.submission, 'rejected', False):
        return RedirectResponse(f"/admin/review/{it.submission_id}?msg=该提交已被驳回，不能操作条目", status_code=302)
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
    if it.submission and getattr(it.submission, 'rejected', False):
        return RedirectResponse(f"/admin/review/{it.submission_id}?msg=该提交已被驳回，不能操作条目", status_code=302)
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


@router.post("/admin/review/{sub_id}/reject")
def admin_reject_submission(sub_id: int, reason: str = Form(""), background_tasks: BackgroundTasks = None, db = Depends(get_db), current_user = Depends(get_current_user)):
    """驳回整条提交：直接应用驳回。"""
    require_admin_or_reviewer(current_user)
    sub = db.get(Submission, sub_id)
    if not sub or sub.is_deleted:
        return RedirectResponse("/admin/review?msg=提交不存在或已删除", status_code=302)
    if getattr(sub, 'rejected', False):
        return RedirectResponse(f"/admin/review/{sub_id}?msg=已处于驳回状态", status_code=302)
    r = (reason or '').strip() or '未填写理由'
    sub.rejected = True
    sub.rejected_reason = r
    sub.rejected_at = now_tokyo()
    sub.rejected_by_id = current_user.id
    sub.manual_points = None
    # 构造更可读标题内容
    event_name = sub.event.name if sub.event else '活动'
    # 列出前三个题目名
    ch_names = []
    try:
        for it in sub.items[:3]:
            if it.challenge and it.challenge.name:
                ch_names.append(it.challenge.name)
    except Exception:
        pass
    ch_part = (" · ".join(ch_names)) if ch_names else "提交"
    title = f"提交被驳回 - {event_name}"
    content = f"您的提交 {event_name} 已被驳回。\n\n理由：\n{r}"
    db.add(Notification(user_id=sub.user_id, type='rejection', title=title, content=content, related_id=sub.id))
    # 邮件通知（同步，可失败）
    if sub.user and sub.user.email:
        if background_tasks is not None:
            background_tasks.add_task(_bg_send_email, sub.user.email, title, content)
        else:
            try:
                import threading
                threading.Thread(target=_bg_send_email, args=(sub.user.email, title, content), daemon=True).start()
            except Exception:
                pass
    db.commit()
    return RedirectResponse(f"/admin/review/{sub_id}?msg=已驳回并发送通知", status_code=302)


@router.get("/admin/review/{sub_id}/reject_confirm", response_class=HTMLResponse)
def admin_reject_confirm(sub_id: int, request: Request, db = Depends(get_db), current_user = Depends(get_current_user)):
    require_admin_or_reviewer(current_user)
    sub = db.get(Submission, sub_id)
    if not sub or sub.is_deleted:
        raise HTTPException(404, "提交不存在")
    reason = (request.query_params.get('reason') or '').strip()
    if not reason:
        return RedirectResponse(f"/admin/review/{sub_id}?msg=理由缺失，重新提交", status_code=302)
    return render_template("admin_reject_confirm.html", title="确认驳回", current_user=current_user, sub=sub, reason=reason)


@router.post("/admin/review/{sub_id}/reject_apply")
def admin_reject_apply(sub_id: int, reason: str = Form(...), background_tasks: BackgroundTasks = None, db = Depends(get_db), current_user = Depends(get_current_user)):
    require_admin_or_reviewer(current_user)
    sub = db.get(Submission, sub_id)
    if not sub or sub.is_deleted:
        return RedirectResponse("/admin/review?msg=提交不存在或已删除", status_code=302)
    if getattr(sub, 'rejected', False):
        return RedirectResponse(f"/admin/review/{sub_id}?msg=已处于驳回状态", status_code=302)
    from ..models import Notification
    from ..utils import now_tokyo
    reason_clean = (reason or '').strip() or '未填写理由'
    sub.rejected = True
    sub.rejected_reason = reason_clean
    sub.rejected_at = now_tokyo()
    sub.rejected_by_id = current_user.id
    sub.manual_points = None
    # 构造更可读标题内容
    event_name = sub.event.name if sub.event else '活动'
    # 列出前三个题目名
    ch_names = []
    try:
        for it in sub.items[:3]:
            if it.challenge and it.challenge.name:
                ch_names.append(it.challenge.name)
    except Exception:
        pass
    ch_part = (" · ".join(ch_names)) if ch_names else "提交"
    title = f"提交被驳回 - {event_name}"
    content = f"您的提交 {event_name} 已被驳回。\n\n理由：\n{reason_clean}"
    db.add(Notification(user_id=sub.user_id, type='rejection', title=title, content=content, related_id=sub.id))
    if sub.user and sub.user.email:
        if background_tasks is not None:
            background_tasks.add_task(_bg_send_email, sub.user.email, title, content)
        else:
            try:
                import threading
                threading.Thread(target=_bg_send_email, args=(sub.user.email, title, content), daemon=True).start()
            except Exception:
                pass
    db.commit()
    return RedirectResponse(f"/admin/review/{sub_id}?msg=已驳回并发送通知", status_code=302)


@router.post("/admin/review/{sub_id}/unreject")
def admin_unreject_submission(sub_id: int, db = Depends(get_db), current_user = Depends(get_current_user)):
    """取消驳回：清除驳回标记并将关联驳回通知移入垃圾箱。"""
    require_admin_or_reviewer(current_user)
    sub = db.get(Submission, sub_id)
    if not sub or sub.is_deleted:
        return RedirectResponse("/admin/review?msg=提交不存在或已删除", status_code=302)
    if not getattr(sub, 'rejected', False):
        return RedirectResponse(f"/admin/review/{sub_id}?msg=该提交不在驳回状态", status_code=302)
    # 清除驳回状态
    sub.rejected = False
    sub.rejected_reason = None
    sub.rejected_at = None
    sub.rejected_by_id = None
    # 将相关驳回通知软删除
    notifs = db.query(Notification).filter(Notification.type == 'rejection', Notification.related_id == sub_id, Notification.is_deleted == False).all()
    for n in notifs:
        n.is_deleted = True
    db.commit()
    return RedirectResponse(f"/admin/review/{sub_id}?msg=已取消驳回", status_code=302)


@router.get("/admin/events", response_class=HTMLResponse)
def admin_events(request: Request, db = Depends(get_db), current_user = Depends(get_current_user)):
    require_admin_or_reviewer(current_user)
    events = db.query(Event).filter(Event.is_deleted == False).all()
    types = db.query(EventType).filter(EventType.is_deleted == False, EventType.is_active == True).order_by(EventType.name.asc()).all()
    now = datetime.now(TZ)
    # 排序规则：
    # 1) 有时间范围的优先（同时有 start_time 和 end_time）
    # 2) 正在进行的优先（start <= now < end）
    # 3) 结束/未来的次之（就近原则：最近结束/最近开始的更靠前）
    # 4) 无时间范围的最后
    def _aware(dt):
        if not dt:
            return None
        try:
            if dt.tzinfo is None:
                return dt.replace(tzinfo=TZ)
            return dt.astimezone(TZ)
        except Exception:
            return dt

    def sort_key(e: Event):
        has_range = 1 if (e.start_time and e.end_time) else 0
        # 分组优先级：0 进行中；1 结束/未来；2 无时间范围
        if has_range:
            s = _aware(e.start_time)
            ed = _aware(e.end_time)
            if s and ed and s <= now and now < ed:
                group = 0
                dist = (now - s).total_seconds() if s else 0
            else:
                group = 1
                if ed and ed < now:  # 已结束：越接近现在越靠前
                    dist = (now - ed).total_seconds()
                else:  # 未来：越临近开始越靠前
                    dist = (s - now).total_seconds() if s else 0
        else:
            group = 2
            dist = float('inf')
        # Python 默认升序：希望优先级低的数值更小，因此 group 已按优先度编码
        # has_range 为 1 的应该靠前，因此取 -has_range 作为第一关键字
        return (-has_range, group, dist, e.id * -1)

    events.sort(key=sort_key)
    return render_template("admin_events.html", title="比赛管理", current_user=current_user, events=events, year=now.year, month=now.month, types=types)


@router.post("/admin/events/create")
def admin_create_event(request: Request, name: str = Form(...), start: str = Form(""), end: str = Form(""), weight: float = Form(1.0), event_type_id: int = Form(None), is_reproduction: int = Form(0), allow_wp_only: int = Form(0), remark: str = Form(""), db = Depends(get_db), current_user = Depends(get_current_user)):
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
        remark=(remark or '').strip() or None,
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
def admin_update_event(event_id: int, name: str = Form(...), start: str = Form(""), end: str = Form(""), weight: float = Form(1.0), event_type_id: int = Form(None), is_reproduction: int = Form(0), allow_wp_only: int = Form(0), remark: str = Form(""), db = Depends(get_db), current_user = Depends(get_current_user)):
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
    evt.remark = (remark or '').strip() or None
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
    # 改为“按提交列一行”视图
    subs = (
        db.query(Submission)
        .filter(Submission.event_id == event_id, Submission.is_deleted == False)
        .order_by(Submission.created_at.desc())
        .all()
    )
    rows = []
    for s in subs:
        total_items = len(s.items)
        count_ok = sum(1 for it in s.items if it.approved and not it.revoked)
        count_pending = sum(1 for it in s.items if not it.approved)
        count_revoked = sum(1 for it in s.items if it.revoked)
        manual_set = (getattr(s, 'manual_points', None) is not None)
        is_reviewed = (total_items > 0 and count_pending == 0) or (total_items == 0 and manual_set)
        ch_names = []
        for it in s.items:
            try:
                if it.challenge and it.challenge.name:
                    ch_names.append(it.challenge.name)
            except Exception:
                pass
        rows.append({
            "sub_id": s.id,
            "created_at": s.created_at,
            "username": s.user.username if s.user else "—",
            "points": compute_submission_points(s),
            "count_ok": count_ok,
            "count_pending": count_pending,
            "count_revoked": count_revoked,
            "reviewed": is_reviewed,
            "challenges": ch_names,
        })
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
    # 动态题目类别：Setting 表 challenge_categories（以逗号或换行分隔），无则使用默认常量
    cats_setting = db.get(Setting, 'challenge_categories')
    if cats_setting and cats_setting.value.strip():
        import re
        raw = cats_setting.value.strip()
        parts = [p.strip() for p in re.split(r'[\n,]+', raw) if p.strip()]
        categories = parts if parts else CATEGORIES
    else:
        categories = CATEGORIES
    return render_template("admin_challenges.html", title="题目管理", current_user=current_user, event=event, challenges=challenges, categories=categories, msg=request.query_params.get("msg"), q=q, cat=cat)


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
def admin_update_challenge(event_id: int, ch_id: int, name: str = Form(...), category: str = Form(...), base_score: int = Form(...), db = Depends(get_db), current_user = Depends(get_current_user)):
    require_admin_or_reviewer(current_user)
    ch = db.get(Challenge, ch_id)
    if not ch or ch.event_id != event_id:
        raise HTTPException(404, "题目不存在")
    ch.name = (name or '').strip() or ch.name
    ch.category = (category or '').strip() or ch.category
    ch.base_score = int(base_score)
    db.commit()
    return RedirectResponse(f"/admin/events/{event_id}/challenges?msg=分值已更新", status_code=302)


@router.post("/admin/review/{sub_id}/set_points")
def admin_set_submission_points(sub_id: int, manual_points: float = Form(None), clear: int = Form(0), db = Depends(get_db), current_user = Depends(get_current_user)):
    require_admin_or_reviewer(current_user)
    sub = db.get(Submission, sub_id)
    if not sub:
        raise HTTPException(404, "提交不存在")
    # 驳回状态下禁止设置或清除手动分数
    if getattr(sub, 'rejected', False):
        return RedirectResponse(f"/admin/review/{sub_id}?msg=该提交已被驳回，不能设置得分", status_code=302)
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


# 邮件配置管理
@router.get("/admin/email", response_class=HTMLResponse)
def admin_email_settings(request: Request, db = Depends(get_db), current_user = Depends(get_current_user)):
    require_admin(current_user)
    def getv(k, default=''):
        s = db.get(Setting, k)
        return s.value if s else default
    ctx = {
        'email_enabled': (getv('email_enabled', '0') in ('1', 'true', 'True')),
        'smtp_host': getv('smtp_host', ''),
        'smtp_port': getv('smtp_port', '587'),
        'smtp_user': getv('smtp_user', ''),
        'smtp_password': getv('smtp_password', ''),
        'smtp_from': getv('smtp_from', ''),
    }
    return render_template("admin_email.html", title="邮件配置", current_user=current_user, **ctx, msg=request.query_params.get('msg'))


@router.post("/admin/email")
def admin_email_settings_save(email_enabled: int = Form(0), smtp_host: str = Form(""), smtp_port: str = Form("587"), smtp_user: str = Form(""), smtp_password: str = Form(""), smtp_from: str = Form(""), db = Depends(get_db), current_user = Depends(get_current_user)):
    require_admin(current_user)
    def setv(k, v):
        s = db.get(Setting, k)
        if s:
            s.value = v
        else:
            db.add(Setting(key=k, value=v))
    setv('email_enabled', '1' if int(email_enabled or 0) == 1 else '0')
    setv('smtp_host', (smtp_host or '').strip())
    setv('smtp_port', (smtp_port or '').strip() or '587')
    setv('smtp_user', (smtp_user or '').strip())
    setv('smtp_password', (smtp_password or '').strip())
    setv('smtp_from', (smtp_from or '').strip())
    db.commit()
    return RedirectResponse("/admin/email?msg=已保存", status_code=302)


@router.post("/admin/email/test")
def admin_email_test(to: str = Form(...), db = Depends(get_db), current_user = Depends(get_current_user)):
    """测试邮件：后台异步发送，不阻塞请求；仅做基本校验与配置检查。"""
    require_admin(current_user)
    addr = (to or '').strip()
    import re, threading
    if not re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", addr):
        return RedirectResponse("/admin/email?msg=测试地址格式不正确", status_code=302)
    # 基本配置检查（不在此处发送）
    def getv(k, default=''):
        s = db.get(Setting, k)
        return s.value if s else default
    host = (getv('smtp_host') or '').strip()
    user = (getv('smtp_user') or '').strip()
    from_addr = (getv('smtp_from') or user or '').strip()
    if not host or not from_addr:
        return RedirectResponse("/admin/email?msg=主机或发件人未配置", status_code=302)
    # 使用统一的后台发送方法，在线程内创建独立的 Session，避免跨线程复用 db
    def _bg():
        try:
            _bg_send_email(addr, "[CloudEver] 测试邮件", "这是一封用于验证 SMTP 配置的测试邮件。")
        except Exception:
            pass
    threading.Thread(target=_bg, daemon=True).start()
    return RedirectResponse("/admin/email?msg=测试邮件任务已提交(稍后查收)", status_code=302)


# 题目类别动态管理
@router.get("/admin/categories", response_class=HTMLResponse)
def admin_categories_page(request: Request, db = Depends(get_db), current_user = Depends(get_current_user)):
    require_admin(current_user)
    s = db.get(Setting, 'challenge_categories')
    raw = s.value if s else ''
    # 展示为每行一个
    if raw and ',' in raw and '\n' not in raw:
        # 兼容旧格式用逗号分隔：全部替换成换行
        raw_display = '\n'.join([p.strip() for p in raw.split(',') if p.strip()])
    else:
        raw_display = raw or ''
    return render_template("admin_categories.html", title="题目类别", current_user=current_user, raw_categories=raw_display, msg=request.query_params.get('msg'))


@router.post("/admin/categories")
def admin_categories_save(content: str = Form(""), db = Depends(get_db), current_user = Depends(get_current_user)):
    require_admin(current_user)
    import re
    parts = [p.strip() for p in re.split(r'[\n,]+', (content or '').strip()) if p.strip()]
    # 去重保持顺序
    seen = set(); ordered = []
    for p in parts:
        if p not in seen:
            seen.add(p); ordered.append(p)
    value = ','.join(ordered)
    s = db.get(Setting, 'challenge_categories')
    if s:
        s.value = value
    else:
        db.add(Setting(key='challenge_categories', value=value))
    db.commit()
    return RedirectResponse("/admin/categories?msg=已保存", status_code=302)


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
    # 通知（垃圾箱）：按 batch 分组
    trashed_notifs_q = db.query(Notification).filter(Notification.is_deleted == True).order_by(Notification.created_at.desc()).all()
    notif_groups = {}
    for n in trashed_notifs_q:
        bid = n.batch_id or f"single-{n.id}"
        g = notif_groups.get(bid)
        if not g:
            notif_groups[bid] = {
                'batch_id': bid,
                'title': n.title or '通知',
                'created_at': n.created_at,
                'total_count': 0,
                'read_count': 0,
            }
            g = notif_groups[bid]
        g['total_count'] += 1
        if n.read_at is not None:
            g['read_count'] += 1
    trashed_notifs = sorted(list(notif_groups.values()), key=lambda r: r['created_at'], reverse=True)
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
        trashed_notifs=trashed_notifs,
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


@router.post("/admin/trash/notification/{batch_id}/restore")
def trash_restore_notification(batch_id: str, db = Depends(get_db), current_user = Depends(get_current_user)):
    require_admin(current_user)
    notifs = db.query(Notification).filter(Notification.batch_id == batch_id, Notification.is_deleted == True).all()
    if not notifs:
        return RedirectResponse("/admin/trash?msg=该通知组不存在或已恢复", status_code=302)
    for n in notifs:
        n.is_deleted = False
    db.commit()
    return RedirectResponse("/admin/trash?msg=已恢复通知组", status_code=302)


@router.post("/admin/trash/notification/{batch_id}/purge")
def trash_purge_notification(batch_id: str, db = Depends(get_db), current_user = Depends(get_current_user)):
    require_admin(current_user)
    notifs = db.query(Notification).filter(Notification.batch_id == batch_id, Notification.is_deleted == True).all()
    if not notifs:
        return RedirectResponse("/admin/trash?msg=该通知组不存在", status_code=302)
    for n in notifs:
        db.delete(n)
    db.commit()
    return RedirectResponse("/admin/trash?msg=已彻底删除通知组", status_code=302)


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
def admin_users(request: Request, page: int = 1, db = Depends(get_db), current_user = Depends(get_current_user)):
    # 审核员可浏览成员列表（只读），管理员可编辑
    require_admin_or_reviewer(current_user)
    page_size = 50
    page = max(1, int(page or 1))
    base_q = db.query(User).filter(User.is_deleted == False).order_by(User.username.asc())
    total = base_q.count()
    users = base_q.offset((page-1)*page_size).limit(page_size).all()
    total_pages = (total + page_size - 1) // page_size
    return render_template("admin_users.html", title="成员管理", current_user=current_user, users=users, page=page, total_pages=total_pages, total=total)


@router.get("/admin/users/{uid}", response_class=HTMLResponse)
def admin_user_detail(uid: int, request: Request, page: int = 1, db = Depends(get_db), current_user = Depends(get_current_user)):
    # 审核员可浏览成员详情（只读），管理员可编辑
    require_admin_or_reviewer(current_user)
    u = db.get(User, uid)
    if not u or u.is_deleted:
        raise HTTPException(404, "用户不存在")
    page_size = 10
    page = max(1, int(page or 1))
    # 全量查询（不分页）用于统计
    all_subs_q = db.query(Submission).filter(Submission.user_id == uid, Submission.is_deleted == False)
    total_subs = all_subs_q.count()
    all_subs = all_subs_q.all()
    # 全量统计（不随分页变化）
    total_items = sum(len(s.items) for s in all_subs)
    approved = sum(1 for s in all_subs for it in s.items if it.approved and not it.revoked)
    pending = sum(1 for s in all_subs for it in s.items if not it.approved)
    revoked = sum(1 for s in all_subs for it in s.items if it.revoked)
    # 当前页数据
    base_q = db.query(Submission).filter(Submission.user_id == uid, Submission.is_deleted == False).order_by(Submission.created_at.desc())
    subs = base_q.offset((page-1)*page_size).limit(page_size).all()
    rows = []
    for s in subs:
        total_items_s = len(s.items)
        pending_s = sum(1 for it in s.items if not it.approved)
        ok_s = sum(1 for it in s.items if it.approved and not it.revoked)
        rev_s = sum(1 for it in s.items if it.revoked)
        manual_set = (getattr(s, 'manual_points', None) is not None)
        is_reviewed = getattr(s, 'rejected', False) or (total_items_s > 0 and pending_s == 0) or (total_items_s == 0 and manual_set)
        pts = compute_submission_points(s) if (is_reviewed and not getattr(s, 'rejected', False)) else None
        ch_names = []
        try:
            for it in s.items:
                if it.challenge and it.challenge.name:
                    ch_names.append(it.challenge.name)
        except Exception:
            pass
        rows.append({
            "id": s.id,
            "created_at": s.created_at,
            "event": s.event.name if s.event else '—',
            "rejected": getattr(s, 'rejected', False),
            "reviewed": is_reviewed,
            "points": pts,
            "challenges": ch_names,
            "pending": pending_s,
            "ok": ok_s,
            "rev": rev_s,
        })
    total_pages = (total_subs + page_size - 1) // page_size
    return render_template("admin_user_detail.html", title=f"成员详情 — {u.username}", current_user=current_user, user=u, subs=subs, total_items=total_items, approved=approved, pending=pending, revoked=revoked, rows=rows, page=page, total_pages=total_pages, total=total_subs)


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
def admin_update_user(uid: int, role: str = Form(...), team_type: str = Form(...), show_on_leaderboard: int = Form(None), db = Depends(get_db), current_user = Depends(get_current_user)):
    require_admin(current_user)
    u = db.get(User, uid)
    if not u or u.is_deleted:
        raise HTTPException(404, "用户不存在")
    u.role = role if role in ("member", "reviewer", "admin") else u.role
    u.team_type = team_type if team_type in ("main", "sub") else u.team_type
    if show_on_leaderboard is not None:
        u.show_on_leaderboard = bool(int(show_on_leaderboard))
    db.commit()
    return RedirectResponse("/admin/users?msg=已更新", status_code=302)

@router.post("/admin/users/{uid}/email")
def admin_set_user_email(uid: int, email: str = Form(""), db = Depends(get_db), current_user = Depends(get_current_user)):
    require_admin(current_user)
    u = db.get(User, uid)
    if not u or u.is_deleted:
        raise HTTPException(404, "用户不存在")
    e = (email or '').strip()
    if e:
        import re
        if not re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", e):
            return RedirectResponse(f"/admin/users/{uid}?msg=邮箱格式不正确", status_code=302)
    u.email = e or None
    db.commit()
    return RedirectResponse(f"/admin/users/{uid}?msg=邮箱已更新", status_code=302)


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
