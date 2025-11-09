from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from ..deps import get_db, get_current_user, render_template, require_login
from ..models import Notification, Submission
from ..utils import md_to_html

router = APIRouter()


@router.get("/notifications", response_class=HTMLResponse)
def notifications_inbox(request: Request, status: str = "unread", page: int = 1, db = Depends(get_db), current_user = Depends(get_current_user)):
    require_login(current_user)
    page = max(1, int(page or 1))
    page_size = 10  # 固定每页10条
    # 过滤掉被删除的通知；删除后仅在垃圾箱显示
    q = db.query(Notification).filter(Notification.user_id == current_user.id, Notification.is_deleted == False)
    if status == 'unread':
        q = q.filter(Notification.read_at == None)
    elif status == 'read':
        q = q.filter(Notification.read_at != None)
    total = q.count()
    # 全局未读数量用于“全部标记为已读”按钮显示控制
    unread_total = db.query(Notification).filter(Notification.user_id == current_user.id, Notification.is_deleted == False, Notification.read_at == None).count()
    rows = q.order_by(Notification.created_at.desc()).offset((page - 1) * page_size).limit(page_size).all()
    items = [
        {
            'id': n.id,
            'title': (n.title or '通知'),
            'content': n.content,
            'created_at': n.created_at,
            'read': n.read_at is not None,
            'type': n.type,
            'related_id': n.related_id,
        }
        for n in rows
    ]
    return render_template(
        "notifications.html",
        title="通知系统",
        current_user=current_user,
        items=items,
        status=status,
        page=page,
        page_size=page_size,
        total=total,
        unread_total=unread_total,
    )


@router.get("/notifications/{nid}", response_class=HTMLResponse)
def notification_detail(nid: int, request: Request, db = Depends(get_db), current_user = Depends(get_current_user)):
    require_login(current_user)
    n = db.get(Notification, nid)
    if not n or n.is_deleted or n.user_id != current_user.id:
        raise HTTPException(404, "通知不存在")
    # 打开即标记已读
    if n.read_at is None:
        from ..utils import now_tokyo
        n.read_at = now_tokyo(); db.commit()
    # 组装辅助信息（如是驳回通知）
    sub = None
    event_name = None
    ch_names = []
    if n.type == 'rejection' and n.related_id:
        sub = db.get(Submission, n.related_id)
        if sub and sub.event:
            event_name = sub.event.name
            try:
                ch_names = [it.challenge.name for it in sub.items if it.challenge and it.challenge.name]
            except Exception:
                ch_names = []
    content_html = md_to_html(n.content)
    # 回到列表的状态与页码
    status = request.query_params.get('status') or 'unread'
    try:
        page = int(request.query_params.get('page') or 1)
    except Exception:
        page = 1
    return render_template(
        "notification_detail.html",
        title=n.title or "通知",
        current_user=current_user,
        n=n,
        content_html=content_html,
        sub=sub,
        event_name=event_name,
        ch_names=ch_names,
        status=status,
        page=page,
    )
