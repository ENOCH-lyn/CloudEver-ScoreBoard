from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse

from ..deps import get_db, get_current_user, render_template
from ..models import Event, Submission, SubmissionItem, User, Announcement, Setting
from ..utils import leaderboard_month_and_total, md_to_html, compute_submission_points
from ..config import TZ


router = APIRouter()


@router.get("/", response_class=HTMLResponse)
def index(request: Request, year: Optional[int] = None, month: Optional[int] = None, db = Depends(get_db), current_user = Depends(get_current_user)):
    now = datetime.now(TZ)
    year = int(year or now.year)
    month = int(month or now.month)

    main_rows = leaderboard_month_and_total(db, year, month, team_type="main")
    sub_rows = leaderboard_month_and_total(db, year, month, team_type="sub")

    events = db.query(Event).filter(Event.is_active == True, Event.is_deleted == False).all()
    anns = (
        db.query(Announcement)
        .filter(Announcement.visible == True, Announcement.is_deleted == False)
        .order_by(Announcement.updated_at.desc())
        .all()
    )

    return render_template(
        "leaderboard.html",
        title="积分榜",
        current_user=current_user,
        year=year,
        month=month,
        main_rows=main_rows,
        sub_rows=sub_rows,
        events=events,
        announcements=anns,
    )


@router.get("/rules", response_class=HTMLResponse)
def rules_page(request: Request, db = Depends(get_db), current_user = Depends(get_current_user)):
    # 规则内容支持管理员编辑，存储于 settings.rules_md
    rules_md = None
    s = db.get(Setting, 'rules_md')
    if s:
        rules_md = s.value
    rules_html = md_to_html(rules_md) if rules_md else None
    return render_template("rules.html", title="战队规则", current_user=current_user, rules_html=rules_html)


@router.get("/submission/{sub_id}", response_class=HTMLResponse)
def submission_detail(sub_id: int, request: Request, db = Depends(get_db), current_user = Depends(get_current_user)):
    sub = db.get(Submission, sub_id)
    if not sub or sub.is_deleted:
        raise HTTPException(404, "提交不存在")
    items = db.query(SubmissionItem).filter(SubmissionItem.submission_id == sub_id).all()
    # 普通成员查看他人提交时不展示 WP 与外链
    can_view_wp = False
    if current_user:
        if current_user.id == sub.user_id or current_user.role in ("admin", "reviewer"):
            can_view_wp = True
    wp_html = md_to_html(sub.wp_md) if (sub.wp_md and can_view_wp) else None
    return render_template(
        "submission_detail.html",
        title="提交详情",
        current_user=current_user,
        sub=sub,
        user=sub.user,
        event=sub.event,
        items=items,
        wp_html=wp_html,
        can_view_wp=can_view_wp,
    )


@router.get("/announcement/{ann_id}", response_class=HTMLResponse)
def announcement_detail(ann_id: int, request: Request, db = Depends(get_db), current_user = Depends(get_current_user)):
    ann = db.get(Announcement, ann_id)
    if not ann or ann.is_deleted or not ann.visible:
        raise HTTPException(404, "公告不存在或不可见")
    content_html = md_to_html(ann.content)
    return render_template("announcement_detail.html", title=ann.title, current_user=current_user, ann=ann, content_html=content_html)


@router.get("/user/{uid}", response_class=HTMLResponse)
def user_profile(uid: int, request: Request, year: Optional[int] = None, month: Optional[int] = None, db = Depends(get_db), current_user = Depends(get_current_user)):
    u = db.get(User, uid)
    if not u or u.is_deleted:
        raise HTTPException(404, "用户不存在")
    now = datetime.now(TZ)
    year = int(year or now.year)
    month = int(month or now.month)

    start = datetime(year, month, 1, tzinfo=TZ)
    end = datetime(year + 1, 1, 1, tzinfo=TZ) if month == 12 else datetime(year, month + 1, 1, tzinfo=TZ)

    subs_month = (
        db.query(Submission)
        .filter(Submission.user_id == uid)
        .filter(Submission.is_deleted == False)
        .filter(Submission.created_at >= start, Submission.created_at < end)
        .all()
    )
    subs_total = db.query(Submission).filter(Submission.user_id == uid, Submission.is_deleted == False).all()

    def sum_points(subs):
        return sum(compute_submission_points(s) for s in subs)

    details = []
    for s in subs_month:
        count_ok = sum(1 for it in s.items if it.approved and not it.revoked)
        count_pending = sum(1 for it in s.items if not it.approved)
        count_revoked = sum(1 for it in s.items if it.revoked)
        # 展示本次提交涉及的题目名称（不暴露 WP）
        ch_names = []
        for it in s.items:
            try:
                if it.challenge and it.challenge.name:
                    ch_names.append(it.challenge.name)
            except Exception:
                pass
        details.append({
            "sub_id": s.id,
            "created_at": s.created_at,
            "event_name": s.event.name if s.event else "—",
            "count_ok": count_ok,
            "count_pending": count_pending,
            "count_revoked": count_revoked,
            "points": compute_submission_points(s),
            "wp_url": s.wp_url,
            "challenges": ch_names,
        })

    return render_template(
        "user_profile.html",
        title=f"成员 {u.username}",
        current_user=current_user,
        user=u,
        year=year,
        month=month,
        month_points=sum_points(subs_month),
        total_points=sum_points(subs_total),
        details=details,
    )
