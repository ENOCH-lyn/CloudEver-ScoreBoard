from datetime import datetime
from typing import Optional, List, Dict

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse

from ..deps import get_db, get_current_user, render_template
from ..models import Event, Challenge, Submission, SubmissionItem, User
from ..utils import leaderboard_count_approved, md_to_html, compute_submission_points
from ..config import TZ


router = APIRouter()


@router.get("/", response_class=HTMLResponse)
def index(request: Request, year: Optional[int] = None, month: Optional[int] = None, db = Depends(get_db), current_user = Depends(get_current_user)):
    now = datetime.now(TZ)
    year = int(year or now.year)
    month = int(month or now.month)

    main_rows = leaderboard_count_approved(db, year, month, team_type="main")
    sub_rows = leaderboard_count_approved(db, year, month, team_type="sub")

    events = db.query(Event).filter(Event.is_active == True).all()

    return render_template(
        "leaderboard.html",
        title="排行榜",
        current_user=current_user,
        year=year,
        month=month,
        main_rows=main_rows,
        sub_rows=sub_rows,
        events=events,
    )


@router.get("/rules", response_class=HTMLResponse)
def rules_page(request: Request, year: Optional[int] = None, month: Optional[int] = None, db = Depends(get_db), current_user = Depends(get_current_user)):
    now = datetime.now(TZ)
    year = int(year or now.year)
    month = int(month or now.month)

    main_rows = leaderboard_count_approved(db, year, month, team_type="main")
    sub_rows = leaderboard_count_approved(db, year, month, team_type="sub")

    suggestion = None
    if main_rows and sub_rows:
        main_last = sorted(main_rows, key=lambda r: (r["month_points"], r["total_points"]))[0]
        sub_best = sorted(sub_rows, key=lambda r: (r["month_points"], r["total_points"]), reverse=True)[0]
        if sub_best["month_count"] > main_last["month_count"]:
            suggestion = {
                "demote": main_last,
                "promote": sub_best,
                "reason": f"子队 {sub_best['username']} 本月通过题数 {sub_best['month_count']} > 主队末位 {main_last['username']} 本月通过题数 {main_last['month_count']}",
            }

    return render_template("rules.html", title="战队规则", current_user=current_user, year=year, month=month, suggestion=suggestion)


@router.get("/submission/{sub_id}", response_class=HTMLResponse)
def submission_detail(sub_id: int, request: Request, db = Depends(get_db), current_user = Depends(get_current_user)):
    sub = db.get(Submission, sub_id)
    if not sub:
        raise HTTPException(404, "提交不存在")
    items = db.query(SubmissionItem).filter(SubmissionItem.submission_id == sub_id).all()
    wp_html = md_to_html(sub.wp_md)
    return render_template("submission_detail.html", title="提交详情", current_user=current_user, sub=sub, user=sub.user, event=sub.event, items=items, wp_html=wp_html)


@router.get("/user/{uid}", response_class=HTMLResponse)
def user_profile(uid: int, request: Request, year: Optional[int] = None, month: Optional[int] = None, db = Depends(get_db), current_user = Depends(get_current_user)):
    u = db.get(User, uid)
    if not u:
        raise HTTPException(404, "用户不存在")
    now = datetime.now(TZ)
    year = int(year or now.year)
    month = int(month or now.month)

    start = datetime(year, month, 1, tzinfo=TZ)
    end = datetime(year + 1, 1, 1, tzinfo=TZ) if month == 12 else datetime(year, month + 1, 1, tzinfo=TZ)

    subs_month = (
        db.query(Submission)
        .filter(Submission.user_id == uid)
        .filter(Submission.created_at >= start, Submission.created_at < end)
        .all()
    )
    subs_total = db.query(Submission).filter(Submission.user_id == uid).all()

    def count_ok(sub):
        return sum(1 for it in sub.items if it.approved and not it.revoked)

    details = []
    for s in subs_month:
        count_ok = sum(1 for it in s.items if it.approved and not it.revoked)
        count_pending = sum(1 for it in s.items if not it.approved)
        count_revoked = sum(1 for it in s.items if it.revoked)
        details.append({
            "sub_id": s.id,
            "created_at": s.created_at,
            "event_name": s.event.name if s.event else "—",
            "count_ok": count_ok,
            "count_pending": count_pending,
            "count_revoked": count_revoked,
            "wp_url": s.wp_url,
        })

    return render_template(
        "user_profile.html",
        title=f"成员 {u.username}",
        current_user=current_user,
        user=u,
        year=year,
        month=month,
        month_count=sum(count_ok(s) for s in subs_month),
        total_count=sum(count_ok(s) for s in subs_total),
        details=details,
    )
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse

from ..deps import get_db, get_current_user, render_template
from ..models import Event, Submission, SubmissionItem, User
from ..utils import leaderboard_month_and_total, md_to_html
from ..config import TZ


router = APIRouter()


@router.get("/", response_class=HTMLResponse)
def index(request: Request, year: Optional[int] = None, month: Optional[int] = None, db = Depends(get_db), current_user = Depends(get_current_user)):
    now = datetime.now(TZ)
    year = int(year or now.year)
    month = int(month or now.month)

    main_rows = leaderboard_month_and_total(db, year, month, team_type="main")
    sub_rows = leaderboard_month_and_total(db, year, month, team_type="sub")

    events = db.query(Event).filter(Event.is_active == True).all()

    return render_template(
        "leaderboard.html",
        title="积分榜",
        current_user=current_user,
        year=year,
        month=month,
        main_rows=main_rows,
        sub_rows=sub_rows,
        events=events,
    )


@router.get("/rules", response_class=HTMLResponse)
def rules_page(request: Request, year: Optional[int] = None, month: Optional[int] = None, db = Depends(get_db), current_user = Depends(get_current_user)):
    now = datetime.now(TZ)
    year = int(year or now.year)
    month = int(month or now.month)

    main_rows = leaderboard_month_and_total(db, year, month, team_type="main")
    sub_rows = leaderboard_month_and_total(db, year, month, team_type="sub")

    suggestion = None
    if main_rows and sub_rows:
        main_last = sorted(main_rows, key=lambda r: (r["month_points"], r["total_points"]))[0]
        sub_best = sorted(sub_rows, key=lambda r: (r["month_points"], r["total_points"]), reverse=True)[0]
        if sub_best["month_points"] > main_last["month_points"]:
            suggestion = {
                "demote": main_last,
                "promote": sub_best,
                "reason": f"子队 {sub_best['username']} 本月 {sub_best['month_points']:.2f} > 主队末位 {main_last['username']} 本月 {main_last['month_points']:.2f}",
            }

    return render_template("rules.html", title="战队规则", current_user=current_user, year=year, month=month, suggestion=suggestion)


@router.get("/submission/{sub_id}", response_class=HTMLResponse)
def submission_detail(sub_id: int, request: Request, db = Depends(get_db), current_user = Depends(get_current_user)):
    sub = db.get(Submission, sub_id)
    if not sub:
        raise HTTPException(404, "提交不存在")
    items = db.query(SubmissionItem).filter(SubmissionItem.submission_id == sub_id).all()
    wp_html = md_to_html(sub.wp_md)
    return render_template("submission_detail.html", title="提交详情", current_user=current_user, sub=sub, user=sub.user, event=sub.event, items=items, wp_html=wp_html)


@router.get("/user/{uid}", response_class=HTMLResponse)
def user_profile(uid: int, request: Request, year: Optional[int] = None, month: Optional[int] = None, db = Depends(get_db), current_user = Depends(get_current_user)):
    u = db.get(User, uid)
    if not u:
        raise HTTPException(404, "用户不存在")
    now = datetime.now(TZ)
    year = int(year or now.year)
    month = int(month or now.month)

    start = datetime(year, month, 1, tzinfo=TZ)
    end = datetime(year + 1, 1, 1, tzinfo=TZ) if month == 12 else datetime(year, month + 1, 1, tzinfo=TZ)

    subs_month = (
        db.query(Submission)
        .filter(Submission.user_id == uid)
        .filter(Submission.created_at >= start, Submission.created_at < end)
        .all()
    )
    subs_total = db.query(Submission).filter(Submission.user_id == uid).all()

    def sum_points(subs):
        from ..utils import compute_submission_points
        return sum(compute_submission_points(s) for s in subs)

    details = []
    from ..utils import compute_submission_points
    for s in subs_month:
        count_ok = sum(1 for it in s.items if it.approved and not it.revoked)
        count_pending = sum(1 for it in s.items if not it.approved)
        count_revoked = sum(1 for it in s.items if it.revoked)
        details.append({
            "sub_id": s.id,
            "created_at": s.created_at,
            "event_name": s.event.name if s.event else "—",
            "count_ok": count_ok,
            "count_pending": count_pending,
            "count_revoked": count_revoked,
            "points": compute_submission_points(s),
            "wp_url": s.wp_url,
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
