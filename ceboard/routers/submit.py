from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import RedirectResponse, HTMLResponse

from ..deps import get_db, get_current_user, require_login, render_template, await_form
from ..models import Event, Challenge, Submission, SubmissionItem


router = APIRouter()


@router.get("/submit", response_class=HTMLResponse)
def submit_list(request: Request, db = Depends(get_db), current_user = Depends(get_current_user)):
    require_login(current_user)
    events = db.query(Event).filter(Event.is_active == True).all()
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
    wp_url = form.get("wp_url") or None
    wp_md = form.get("wp_md") or None

    sub = Submission(user_id=current_user.id, event_id=event_id, wp_url=wp_url, wp_md=wp_md)
    db.add(sub); db.flush()

    for ch in db.query(Challenge).filter(Challenge.event_id == event_id).all():
        if form.get(f"ch_{ch.id}") is not None:
            db.add(SubmissionItem(submission_id=sub.id, challenge_id=ch.id, approved=False, revoked=False))

    db.commit()
    return RedirectResponse("/submit?msg=提交成功，等待管理员审核后计分", status_code=302)
