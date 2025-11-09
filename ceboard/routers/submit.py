from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import RedirectResponse, HTMLResponse

from ..deps import get_db, get_current_user, require_login, render_template, await_form
from ..models import Event, Challenge, Submission, SubmissionItem
from ..utils import compute_submission_points, now_tokyo
from ..models import Notification


router = APIRouter()


@router.get("/submit", response_class=HTMLResponse)
def submit_list(request: Request, db = Depends(get_db), current_user = Depends(get_current_user)):
    require_login(current_user)
    events = db.query(Event).filter(Event.is_active == True, Event.is_deleted == False).all()
    return render_template("submit_list.html", title="提交成绩", current_user=current_user, events=events)


@router.get("/submit/{event_id}", response_class=HTMLResponse)
def submit_event_page(event_id: int, request: Request, db = Depends(get_db), current_user = Depends(get_current_user)):
    require_login(current_user)
    event = db.get(Event, event_id)
    if not event or not event.is_active:
        raise HTTPException(404, "活动不存在或未启用")
    challenges = db.query(Challenge).filter(Challenge.event_id == event_id).all()
    return render_template("submit_event.html", title=f"提交 {event.name}", current_user=current_user, event=event, challenges=challenges)


@router.post("/submit/{event_id}")
async def submit_event_action(event_id: int, request: Request, db = Depends(get_db), current_user = Depends(get_current_user)):
    require_login(current_user)
    event = db.get(Event, event_id)
    if not event or not event.is_active:
        raise HTTPException(404, "活动不存在或未启用")

    form = await await_form(request)
    wp_url = (form.get("wp_url") or "").strip() or None
    # Sanitize URL: only allow http/https
    if wp_url and not (wp_url.lower().startswith("http://") or wp_url.lower().startswith("https://")):
        wp_url = None
    wp_md = form.get("wp_md") or None

    sub = Submission(user_id=current_user.id, event_id=event_id, wp_url=wp_url, wp_md=wp_md)
    db.add(sub); db.flush()

    for ch in db.query(Challenge).filter(Challenge.event_id == event_id).all():
        if form.get(f"ch_{ch.id}") is not None:
            db.add(SubmissionItem(submission_id=sub.id, challenge_id=ch.id, approved=False, revoked=False))

    db.commit()
    return RedirectResponse("/submit?msg=提交成功，等待管理员审核后计分", status_code=302)


@router.post("/submission/{sub_id}/delete")
def delete_own_submission(sub_id: int, db = Depends(get_db), current_user = Depends(get_current_user)):
    require_login(current_user)
    sub = db.get(Submission, sub_id)
    if not sub or sub.is_deleted:
        return RedirectResponse("/profile?msg=提交不存在或已删除", status_code=302)
    if sub.user_id != current_user.id:
        return RedirectResponse(f"/submission/{sub_id}?msg=无权限", status_code=302)
    # 仅在未通过前允许删除：没有已通过且未撤销的条目，且未设置人工分
    approved_any = any(it.approved and not it.revoked for it in sub.items)
    if approved_any or (sub.manual_points is not None):
        return RedirectResponse(f"/submission/{sub_id}?msg=已通过或已评分，不能删除", status_code=302)
    sub.is_deleted = True
    db.commit()
    return RedirectResponse("/submit?msg=已删除（移入垃圾箱）", status_code=302)


@router.get("/submission/{sub_id}/edit", response_class=HTMLResponse)
def edit_rejected_submission_page(sub_id: int, request: Request, db = Depends(get_db), current_user = Depends(get_current_user)):
    """成员重新编辑被驳回的提交：可修改题目选择、WP 等。"""
    require_login(current_user)
    sub = db.get(Submission, sub_id)
    if not sub or sub.is_deleted or sub.user_id != current_user.id:
        raise HTTPException(404, "提交不存在")
    if not getattr(sub, 'rejected', False):
        raise HTTPException(403, "仅驳回的提交可编辑")
    event = sub.event
    challenges = db.query(Challenge).filter(Challenge.event_id == sub.event_id).all()
    # 现有已选题目集合
    selected_ids = {it.challenge_id for it in sub.items}
    return render_template("submission_edit.html", title="重新编辑提交", current_user=current_user, sub=sub, event=event, challenges=challenges, selected_ids=selected_ids)


@router.post("/submission/{sub_id}/edit")
async def edit_rejected_submission_action(sub_id: int, request: Request, db = Depends(get_db), current_user = Depends(get_current_user)):
    require_login(current_user)
    sub = db.get(Submission, sub_id)
    if not sub or sub.is_deleted or sub.user_id != current_user.id:
        raise HTTPException(404, "提交不存在")
    if not getattr(sub, 'rejected', False):
        raise HTTPException(403, "仅驳回的提交可编辑")
    form = await await_form(request)
    wp_url = (form.get("wp_url") or "").strip() or None
    if wp_url and not (wp_url.lower().startswith("http://") or wp_url.lower().startswith("https://")):
        wp_url = None
    wp_md = form.get("wp_md") or None
    # 清空旧题目条目
    for it in list(sub.items):
        db.delete(it)
    db.flush()
    for ch in db.query(Challenge).filter(Challenge.event_id == sub.event_id).all():
        if form.get(f"ch_{ch.id}") is not None:
            db.add(SubmissionItem(submission_id=sub.id, challenge_id=ch.id, approved=False, revoked=False))
    # 重置驳回状态，等待重新审核
    sub.rejected = False
    sub.rejected_reason = None
    sub.rejected_at = None
    sub.rejected_by_id = None
    sub.wp_url = wp_url
    sub.wp_md = wp_md
    sub.manual_points = None
    db.commit()
    return RedirectResponse(f"/submission/{sub.id}?msg=已更新，等待重新审核", status_code=302)


@router.get("/my/submissions", response_class=HTMLResponse)
def my_submissions_page(request: Request, db = Depends(get_db), current_user = Depends(get_current_user)):
    require_login(current_user)
    subs = db.query(Submission).filter(Submission.user_id == current_user.id, Submission.is_deleted == False).order_by(Submission.created_at.desc()).all()
    rows = []
    for s in subs:
        total_items = len(s.items)
        pending_items = sum(1 for it in s.items if not it.approved)
        ok_items = sum(1 for it in s.items if it.approved and not it.revoked)
        rev_items = sum(1 for it in s.items if it.revoked)
        manual_set = (getattr(s, 'manual_points', None) is not None)
        reviewed = manual_set or (total_items > 0 and pending_items == 0)
        rows.append({
            'id': s.id,
            'created_at': s.created_at,
            'event_name': s.event.name if s.event else '—',
            'points': compute_submission_points(s),
            'rejected': getattr(s, 'rejected', False),
            'pending_items': pending_items,
            'ok_items': ok_items,
            'rev_items': rev_items,
            'reviewed': reviewed,
        })
    return render_template("my_submissions.html", title="我的提交记录", current_user=current_user, rows=rows)


@router.post("/notifications/{notif_id}/read")
def mark_notification_read(notif_id: int, request: Request, db = Depends(get_db), current_user = Depends(get_current_user)):
    """标记单条通知为已读。仅允许通知所属用户操作。"""
    require_login(current_user)
    notif = db.get(Notification, notif_id)
    if not notif or notif.is_deleted or notif.user_id != current_user.id:
        return RedirectResponse("/profile?msg=通知不存在或无权限", status_code=302)
    if notif.read_at is None:
        notif.read_at = now_tokyo()
        db.commit()
    # 尝试跳回 Referer，否则首页
    ref = request.headers.get("referer") or "/"
    return RedirectResponse(ref, status_code=302)


@router.post("/notifications/read-all")
def mark_all_notifications_read(request: Request, db = Depends(get_db), current_user = Depends(get_current_user)):
    """将当前用户所有未读通知全部标记为已读。"""
    require_login(current_user)
    notifs = db.query(Notification).filter(Notification.user_id == current_user.id, Notification.is_deleted == False, Notification.read_at == None).all()
    ts = now_tokyo()
    for n in notifs:
        n.read_at = ts
    db.commit()
    ref = request.headers.get("referer") or "/"
    return RedirectResponse(ref, status_code=302)
