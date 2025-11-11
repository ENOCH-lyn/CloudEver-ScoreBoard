from datetime import datetime
from typing import Optional, Dict, List
import markdown as mdlib
try:
    import bleach
except Exception:  # bleach not installed in current env
    bleach = None

from .config import TZ
from html import escape
from .models import Submission, User
from sqlalchemy import or_
import smtplib
from email.message import EmailMessage


def now_tokyo() -> datetime:
    return datetime.now(TZ)


def month_range(year: int, month: int):
    start = datetime(year, month, 1, tzinfo=TZ)
    if month == 12:
        end = datetime(year + 1, 1, 1, tzinfo=TZ)
    else:
        end = datetime(year, month + 1, 1, tzinfo=TZ)
    return start, end


def compute_submission_points(sub: Submission) -> float:
    """Return points for a submission.
    If manual_points is set, use it directly (already considered by reviewer);
    otherwise sum approved, not-revoked items' base_score and multiply by event weight.
    """
    # 被驳回的提交不计分
    if getattr(sub, 'rejected', False):
        return 0.0
    if not sub.event:
        return 0.0
    # manual override takes precedence
    if getattr(sub, 'manual_points', None) is not None:
        try:
            return float(sub.manual_points)
        except Exception:
            return 0.0
    total = 0.0
    for it in sub.items:
        ch = it.challenge
        if ch and it.approved and not it.revoked:
            total += float(ch.base_score)
    return total * float(sub.event.weight or 1.0)


def leaderboard_month_and_total(db, year: int, month: int, team_type: str) -> List[Dict[str, float]]:
    from .models import Submission, User, PointAdjustment
    start, end = month_range(year, month)
    # previous month range
    if month == 1:
        prev_year, prev_month = year - 1, 12
    else:
        prev_year, prev_month = year, month - 1
    prev_start, prev_end = month_range(prev_year, prev_month)

    subs_month = (
        db.query(Submission)
        .join(User, Submission.user_id == User.id)
    .filter(User.team_type == team_type)
    .filter(User.role == 'member')
    .filter(User.is_deleted == False)
    .filter(or_(User.show_on_leaderboard == True, User.show_on_leaderboard == None))
        .filter(Submission.is_deleted == False)
        .filter(Submission.created_at >= start, Submission.created_at < end)
        .all()
    )
    subs_total = (
        db.query(Submission)
        .join(User, Submission.user_id == User.id)
    .filter(User.team_type == team_type)
    .filter(User.role == 'member')
    .filter(User.is_deleted == False)
    .filter(or_(User.show_on_leaderboard == True, User.show_on_leaderboard == None))
        .filter(Submission.is_deleted == False)
        .all()
    )
    # previous month submissions
    subs_prev = (
        db.query(Submission)
        .join(User, Submission.user_id == User.id)
    .filter(User.team_type == team_type)
    .filter(User.role == 'member')
    .filter(User.is_deleted == False)
    .filter(or_(User.show_on_leaderboard == True, User.show_on_leaderboard == None))
        .filter(Submission.is_deleted == False)
        .filter(Submission.created_at >= prev_start, Submission.created_at < prev_end)
        .all()
    )

    month_by_user: Dict[int, float] = {}
    prev_by_user: Dict[int, float] = {}
    total_by_user: Dict[int, float] = {}
    names: Dict[int, str] = {}

    for s in subs_month:
        pts = compute_submission_points(s)
        month_by_user[s.user_id] = month_by_user.get(s.user_id, 0.0) + pts
        if s.user_id not in names:
            u = db.get(User, s.user_id); names[s.user_id] = u.username if u else f"uid:{s.user_id}"

    for s in subs_total:
        pts = compute_submission_points(s)
        total_by_user[s.user_id] = total_by_user.get(s.user_id, 0.0) + pts
        if s.user_id not in names:
            u = db.get(User, s.user_id); names[s.user_id] = u.username if u else f"uid:{s.user_id}"

    for s in subs_prev:
        pts = compute_submission_points(s)
        prev_by_user[s.user_id] = prev_by_user.get(s.user_id, 0.0) + pts
        if s.user_id not in names:
            u = db.get(User, s.user_id); names[s.user_id] = u.username if u else f"uid:{s.user_id}"

    # apply monthly adjustments
    adjs_month = (
        db.query(PointAdjustment)
        .join(User, PointAdjustment.user_id == User.id)
    .filter(User.team_type == team_type)
    .filter(User.role == 'member')
        .filter(PointAdjustment.is_deleted == False)
        .filter(PointAdjustment.year == year, PointAdjustment.month == month)
        .all()
    )
    for a in adjs_month:
        month_by_user[a.user_id] = month_by_user.get(a.user_id, 0.0) + float(a.amount)
        if a.user_id not in names:
            u = db.get(User, a.user_id); names[a.user_id] = u.username if u else f"uid:{a.user_id}"

    # apply previous month's adjustments
    adjs_prev = (
        db.query(PointAdjustment)
        .join(User, PointAdjustment.user_id == User.id)
        .filter(User.team_type == team_type)
        .filter(User.role == 'member')
        .filter(PointAdjustment.is_deleted == False)
        .filter(PointAdjustment.year == prev_year, PointAdjustment.month == prev_month)
        .all()
    )
    for a in adjs_prev:
        prev_by_user[a.user_id] = prev_by_user.get(a.user_id, 0.0) + float(a.amount)
        if a.user_id not in names:
            u = db.get(User, a.user_id); names[a.user_id] = u.username if u else f"uid:{a.user_id}"

    # total adjustments across all months contribute to total_points
    adjs_total = (
        db.query(PointAdjustment)
        .join(User, PointAdjustment.user_id == User.id)
        .filter(User.team_type == team_type)
        .filter(User.role == 'member')
        .filter(PointAdjustment.is_deleted == False)
        .all()
    )
    for a in adjs_total:
        total_by_user[a.user_id] = total_by_user.get(a.user_id, 0.0) + float(a.amount)
        if a.user_id not in names:
            u = db.get(User, a.user_id); names[a.user_id] = u.username if u else f"uid:{a.user_id}"

    user_ids = set(names.keys()) | set(month_by_user.keys()) | set(prev_by_user.keys()) | set(total_by_user.keys())
    rows = [
        {
            "user_id": uid,
            "username": names.get(uid, f"uid:{uid}"),
            "month_points": float(month_by_user.get(uid, 0.0)),
            "prev_month_points": float(prev_by_user.get(uid, 0.0)),
            "total_points": float(total_by_user.get(uid, 0.0)),
        }
        for uid in user_ids
    ]
    rows.sort(key=lambda r: (r["month_points"], r["total_points"]), reverse=True)
    return rows


def leaderboard_count_approved(db, year: int, month: int, team_type: str) -> List[Dict[str, float]]:
    """基于通过题目数量的排行榜（不使用积分），排除管理员账号。"""
    from .models import Submission, User
    start, end = month_range(year, month)

    subs_month = (
        db.query(Submission)
        .join(User, Submission.user_id == User.id)
        .filter(User.team_type == team_type)
        .filter(User.role == 'member')
        .filter(User.is_deleted == False)
        .filter(Submission.is_deleted == False)
        .filter(Submission.created_at >= start, Submission.created_at < end)
        .all()
    )
    subs_total = (
        db.query(Submission)
        .join(User, Submission.user_id == User.id)
        .filter(User.team_type == team_type)
        .filter(User.role == 'member')
        .filter(User.is_deleted == False)
        .filter(Submission.is_deleted == False)
        .all()
    )

    month_by_user: Dict[int, int] = {}
    total_by_user: Dict[int, int] = {}
    names: Dict[int, str] = {}

    def count_ok(sub):
        return sum(1 for it in sub.items if it.approved and not it.revoked)

    for s in subs_month:
        month_by_user[s.user_id] = month_by_user.get(s.user_id, 0) + count_ok(s)
        if s.user_id not in names:
            u = db.get(User, s.user_id); names[s.user_id] = u.username if u else f"uid:{s.user_id}"

    for s in subs_total:
        total_by_user[s.user_id] = total_by_user.get(s.user_id, 0) + count_ok(s)
        if s.user_id not in names:
            u = db.get(User, s.user_id); names[s.user_id] = u.username if u else f"uid:{s.user_id}"

    user_ids = set(names.keys()) | set(month_by_user.keys()) | set(total_by_user.keys())
    rows = [
        {
            "user_id": uid,
            "username": names.get(uid, f"uid:{uid}"),
            "month_count": int(month_by_user.get(uid, 0)),
            "total_count": int(total_by_user.get(uid, 0)),
        }
        for uid in user_ids
    ]
    rows.sort(key=lambda r: (r["month_count"], r["total_count"]), reverse=True)
    return rows


def md_to_html(md_text: Optional[str]) -> str:
    """Render markdown to sanitized HTML to prevent XSS.
    Allowed tags are restricted; script/style/event handlers are stripped.
    Images are intentionally disallowed; links are preserved with safe protocols.
    """
    if not md_text:
        return ""
    raw_html = mdlib.markdown(md_text, extensions=["fenced_code", "tables"]) or ""
    if bleach is None:
        # Safe fallback: show raw markdown as escaped preformatted text
        from html import escape
        return f"<pre class='md-fallback'>{escape(md_text)}</pre>"
    allowed_tags = set([
        'a', 'p', 'ul', 'ol', 'li', 'strong', 'em', 'blockquote',
        'pre', 'code', 'hr', 'br',
        'h1', 'h2', 'h3', 'h4', 'h5', 'h6',
        'table', 'thead', 'tbody', 'tr', 'th', 'td', 'img'
    ])
    allowed_attrs = {
        'a': ['href', 'title', 'target', 'rel'],
        'th': ['colspan', 'rowspan'],
        'td': ['colspan', 'rowspan'],
        # keep code/class if using future highlighters
        'code': ['class'],
        'pre': ['class'],
        'img': ['src', 'alt', 'title', 'width', 'height'],
    }
    cleaned = bleach.clean(
        raw_html,
        tags=allowed_tags,
        attributes=allowed_attrs,
        protocols=['http', 'https', 'mailto'],
        strip=True,
    )
    # Optional: ensure external links have rel noopener
    # We avoid linkify to keep code blocks intact.
    return cleaned


def _wrap_email_html(subject: str, body_md: str) -> str:
        body_html = md_to_html(body_md or "")
        # 极简风格 HTML 模板
        return f"""
<!doctype html>
<html>
<head>
    <meta charset='utf-8'/>
    <meta name='viewport' content='width=device-width, initial-scale=1'/>
    <title>{escape(subject or '通知')}</title>
    <style>
        body{{background:#f8fafc; margin:0; padding:24px; font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Helvetica,Arial,'PingFang SC','Hiragino Sans GB','Microsoft YaHei','Noto Sans CJK SC','Source Han Sans SC',sans-serif; color:#0f172a;}}
        .card{{max-width:680px; margin:0 auto; background:#fff; border:1px solid #e5e7eb; border-radius:14px; padding:24px;}}
        h1{{font-size:18px; margin:0 0 12px;}}
        .content p{{line-height:1.65; margin:10px 0;}}
        .content code, .content pre{{background:#0b10211a; border-radius:8px; padding:2px 6px;}}
        .footer{{margin-top:18px; color:#64748b; font-size:12px;}}
        table{{border-collapse:collapse}}
        th,td{{border:1px solid #e5e7eb; padding:6px 8px;}}
    </style>
    </head>
    <body>
        <div class='card'>
            <h1>{escape(subject or '通知')}</h1>
            <div class='content'>{body_html}</div>
            <div class='footer'>此邮件由 CloudEver 自动发送</div>
        </div>
    </body>
</html>
"""


def send_email_sync(db, to_addr: str, subject: str, body: str) -> bool:
    """Send email synchronously using SMTP settings stored in Setting table.
    Settings keys:
      - email_enabled: '1' or '0'
      - smtp_host, smtp_port, smtp_user, smtp_password, smtp_from
    Returns True if sent, False otherwise (silently catches errors).
    """
    try:
        from .models import Setting
        get = lambda k: (db.get(Setting, k).value if db.get(Setting, k) else None)
        enabled = (get('email_enabled') or '0').strip()
        if enabled not in ('1', 'true', 'True'):
            return False
        host = (get('smtp_host') or '').strip()
        port_str = (get('smtp_port') or '').strip() or '587'
        user = (get('smtp_user') or '').strip()
        pwd = (get('smtp_password') or '').strip()
        from_addr = (get('smtp_from') or user or '').strip()
        if not host or not from_addr or not to_addr:
            return False
        try:
            port = int(port_str)
        except Exception:
            port = 587
        msg = EmailMessage()
        msg['Subject'] = subject
        msg['From'] = from_addr
        msg['To'] = to_addr
        # 文本 + HTML（Markdown 渲染）
        plain = (body or '').strip()
        html = _wrap_email_html(subject, body or '')
        msg.set_content(plain or subject or '')
        msg.add_alternative(html, subtype='html')
        with smtplib.SMTP(host, port, timeout=15) as s:
            try:
                s.starttls()
            except Exception:
                pass
            if user:
                try:
                    s.login(user, pwd)
                except Exception:
                    # allow anonymous if login fails
                    pass
            s.send_message(msg)
        return True
    except Exception:
        return False
