from datetime import datetime
from typing import Optional, Dict, List
import markdown as mdlib
try:
    import bleach
except Exception:  # bleach not installed in current env
    bleach = None

from .config import TZ
from .models import Submission


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
    if not sub.event:
        return 0.0
    total = 0.0
    for it in sub.items:
        ch = it.challenge
        if ch and it.approved and not it.revoked:
            total += float(ch.base_score)
    return total * float(sub.event.weight or 1.0)


def leaderboard_month_and_total(db, year: int, month: int, team_type: str) -> List[Dict[str, float]]:
    from .models import Submission, User
    start, end = month_range(year, month)

    subs_month = (
        db.query(Submission)
        .join(User, Submission.user_id == User.id)
        .filter(User.team_type == team_type)
        .filter(User.role != 'admin')
        .filter(Submission.created_at >= start, Submission.created_at < end)
        .all()
    )
    subs_total = (
        db.query(Submission)
        .join(User, Submission.user_id == User.id)
        .filter(User.team_type == team_type)
        .filter(User.role != 'admin')
        .all()
    )

    month_by_user: Dict[int, float] = {}
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

    user_ids = set(names.keys()) | set(month_by_user.keys()) | set(total_by_user.keys())
    rows = [
        {
            "user_id": uid,
            "username": names.get(uid, f"uid:{uid}"),
            "month_points": float(month_by_user.get(uid, 0.0)),
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
        .filter(User.role != 'admin')
        .filter(Submission.created_at >= start, Submission.created_at < end)
        .all()
    )
    subs_total = (
        db.query(Submission)
        .join(User, Submission.user_id == User.id)
        .filter(User.team_type == team_type)
        .filter(User.role != 'admin')
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
        'table', 'thead', 'tbody', 'tr', 'th', 'td'
    ])
    allowed_attrs = {
        'a': ['href', 'title', 'target', 'rel'],
        'th': ['colspan', 'rowspan'],
        'td': ['colspan', 'rowspan'],
        # keep code/class if using future highlighters
        'code': ['class'],
        'pre': ['class'],
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
