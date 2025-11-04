from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional

from fastapi import Depends, FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from starlette.middleware.sessions import SessionMiddleware
from starlette import status

from jinja2 import Environment, DictLoader, select_autoescape

from sqlalchemy import (
    Column, DateTime, Float, ForeignKey, Integer, String,
    Text, Boolean, create_engine
)
from sqlalchemy.orm import declarative_base, relationship, Session, sessionmaker

from passlib.hash import pbkdf2_sha256 as pwdhash

# Markdown 渲染
import markdown as mdlib

# ---------------------------------------------------
# 基础配置
# ---------------------------------------------------
DATABASE_URL = "sqlite:///./ctf_scoring.db"
SESSION_SECRET = "请修改我-非常重要"  # 生产环境务必修改！
TZ = timezone(timedelta(hours=9))  # 亚洲/东京（UTC+9）

# 类别（仅用于标识，不再有可配置权重）
CATEGORIES = ["web", "pwn", "crypto", "rev", "misc"]

# ---------------------------------------------------
# 数据库模型
# ---------------------------------------------------
Base = declarative_base()
engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

class Setting(Base):
    __tablename__ = "settings"
    key = Column(String, primary_key=True)
    value = Column(Text, nullable=False)


class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True)
    username = Column(String, unique=True, nullable=False)
    password_hash = Column(String, nullable=False)
    role = Column(String, default="member")  # 'admin' or 'member'
    team_type = Column(String, default="sub")  # 'main' or 'sub'
    is_active = Column(Boolean, default=True)

    submissions = relationship("Submission", back_populates="user")

    def check_password(self, pw: str) -> bool:
        try:
            return pwdhash.verify(pw, self.password_hash)
        except Exception:
            return False


class Event(Base):
    __tablename__ = "events"
    id = Column(Integer, primary_key=True)
    name = Column(String, nullable=False)
    start_time = Column(DateTime(timezone=True))
    end_time = Column(DateTime(timezone=True))
    weight = Column(Float, default=1.0)    # 活动整体权重（仍保留）
    is_reproduction = Column(Boolean, default=False)
    is_active = Column(Boolean, default=True)

    challenges = relationship("Challenge", back_populates="event", cascade="all,delete")
    submissions = relationship("Submission", back_populates="event")


class Challenge(Base):
    __tablename__ = "challenges"
    id = Column(Integer, primary_key=True)
    event_id = Column(Integer, ForeignKey("events.id"), nullable=False)
    name = Column(String, nullable=False)
    category = Column(String, default="misc")
    base_score = Column(Integer, default=100)

    event = relationship("Event", back_populates="challenges")


class Submission(Base):
    __tablename__ = "submissions"
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    event_id = Column(Integer, ForeignKey("events.id"), nullable=False)
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(TZ))

    wp_url = Column(String, nullable=True)  # 外链
    wp_md = Column(Text, nullable=True)     # Markdown 内容

    user = relationship("User", back_populates="submissions")
    event = relationship("Event", back_populates="submissions")
    items = relationship("SubmissionItem", back_populates="submission", cascade="all,delete-orphan")


class SubmissionItem(Base):
    __tablename__ = "submission_items"
    id = Column(Integer, primary_key=True)
    submission_id = Column(Integer, ForeignKey("submissions.id"), nullable=False)
    challenge_id = Column(Integer, ForeignKey("challenges.id"), nullable=False)

    # 审核状态：支持复审（可反复切换）
    approved = Column(Boolean, default=False)  # 管理员审核通过
    revoked = Column(Boolean, default=False)   # 管理员撤销此题得分
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(TZ))

    submission = relationship("Submission", back_populates="items")
    challenge = relationship("Challenge")


# ---------------------------------------------------
# 应用与模板（中文 UI）
# ---------------------------------------------------
app = FastAPI(title="CTF 战队考核系统")
app.add_middleware(SessionMiddleware, secret_key=SESSION_SECRET)

TEMPLATES: Dict[str, str] = {
    "base.html": r"""
    <!doctype html>
    <html lang=\"zh-CN\">
    <head>
      <meta charset=\"utf-8\" />
      <meta name=\"viewport\" content=\"width=device-width, initial-scale=1\" />
      <title>{{ title or 'CTF 战队考核系统' }}</title>
      <style>
        :root { --bg1:#0e1630; --bg2:#10192f; --card:rgba(16,25,47,0.75); --stroke:rgba(255,255,255,0.08); --text:#e7eefc; --muted:#a9b8d9; --accent:#6ea8ff; --accent-2:#66e0ff; }
        * { box-sizing: border-box; }
        body { font-family: ui-sans-serif, system-ui, -apple-system, Segoe UI, Roboto, 'Noto Sans SC', Arial; margin:0; padding:0; color:var(--text);
               background: radial-gradient(1200px 600px at 10% 0%, #102045 0%, var(--bg1) 40%, var(--bg2) 100%); min-height:100vh; }
        header { backdrop-filter:saturate(140%) blur(8px); background:linear-gradient(90deg,rgba(16,25,47,0.7),rgba(16,25,47,0.3)); border-bottom:1px solid var(--stroke);
                 padding:12px 18px; display:flex; align-items:center; gap:16px; position:sticky; top:0; z-index:10; }
        a { color: var(--accent); text-decoration: none; }
        a:hover { text-decoration: underline; }
        .wrap { max-width: 1100px; margin: 0 auto; padding: 24px; }
        .nav a { margin-right: 14px; }
        .card { background: var(--card); border: 1px solid var(--stroke); border-radius: 16px; padding: 18px; margin: 14px 0; box-shadow: 0 10px 40px rgba(0,0,0,0.25), inset 0 1px 0 rgba(255,255,255,0.04); }
        .btn { background: linear-gradient(135deg, var(--accent), var(--accent-2)); color: #0a1020; font-weight: 700; border: none; padding: 10px 16px; border-radius: 12px; cursor: pointer; }
        .btn.warn { background: #ff6b6b; color: white; }
        .btn.secondary { background: #22314f; color: var(--text); }
        input, select, textarea { width: 100%; padding: 10px; border-radius: 10px; border: 1px solid var(--stroke); background: rgba(8,14,30,0.6); color: var(--text); }
        label { font-weight: 600; font-size: 14px; }
        table { width:100%; border-collapse: collapse; }
        th, td { padding: 10px; border-bottom: 1px solid var(--stroke); text-align: left; }
        th { background: rgba(16,25,47,0.6); }
        .grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(280px, 1fr)); gap: 16px; }
        .muted { color: var(--muted); }
        .row { display:flex; gap:10px; align-items:center; flex-wrap: wrap; }
        .kpi { font-weight: 800; font-size: 28px; letter-spacing: 0.5px; }
        .pill { padding: 2px 8px; border-radius: 999px; background: rgba(255,255,255,0.06); border: 1px solid var(--stroke); }
        .status { font-size:12px; padding:2px 6px; border-radius:999px; border:1px solid var(--stroke); }
        .pending{ background:rgba(255,255,255,0.06); }
        .ok{ background:rgba(102,224,255,0.15); }
        .rev{ background:rgba(255,107,107,0.18); }
        .md { background: rgba(0,0,0,0.2); padding: 12px; border-radius: 12px; border: 1px solid var(--stroke); overflow-x:auto; }
        .md pre { background: rgba(0,0,0,0.25); padding: 10px; border-radius: 8px; }
        .md code { font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, 'Liberation Mono', monospace; }
      </style>
    </head>
    <body>
      <header>
        <div class="nav">
          <a href="/">积分榜</a>
          <a href="/rules">战队规则</a>
          <a href="/submit">提交成绩</a>
          {% if current_user and current_user.role == 'admin' %}
            <a href="/admin/review">管理：审核</a>
            <a href="/admin/events">管理：活动</a>
            <a href="/admin/users">管理：成员</a>
          {% endif %}
        </div>
        <div style="margin-left:auto">
          {% if current_user %}
            <span class="muted">{{ current_user.username }} · <span class="pill">{{ '主队' if current_user.team_type=='main' else '子队' }}</span></span>
            <form style="display:inline" action="/auth/logout" method="post">
              <button class="btn secondary" type="submit">退出</button>
            </form>
          {% else %}
            <a class="btn secondary" href="/auth/login">登录</a>
            <a class="btn" style="margin-left:8px" href="/auth/register">注册</a>
          {% endif %}
        </div>
      </header>
      <div class="wrap">
        {% if msg %}<div class="card">{{ msg }}</div>{% endif %}
        {% block content %}{% endblock %}
      </div>
    </body>
    </html>
    """,
    "login.html": r"""
    {% extends 'base.html' %}
    {% block content %}
    <div class="card">
      <h2>账号登录</h2>
      <form method="post">
        <label>用户名</label>
        <input name="username" required />
        <label>密码</label>
        <input name="password" type="password" required />
        <div style="margin-top:12px"><button class="btn" type="submit">登录</button></div>
      </form>
    </div>
    {% endblock %}
    """,
    "register.html": r"""
    {% extends 'base.html' %}
    {% block content %}
    <div class="card">
      <h2>注册新成员</h2>
      <form method="post">
        <label>用户名</label>
        <input name="username" required />
        <label>密码</label>
        <input name="password" type="password" required />
        <label>确认密码</label>
        <input name="password2" type="password" required />
        <p class="muted">注册默认加入 <b>子队</b>，管理员可在后台将你调整到主队。</p>
        <div style="margin-top:12px"><button class="btn" type="submit">注册</button></div>
      </form>
    </div>
    {% endblock %}
    """,
    "leaderboard.html": r"""
    {% extends 'base.html' %}
    {% block content %}
    <div class="grid">
      <div class="card">
        <h2>本月总览</h2>
        <form class="row" method="get" action="/">
          <label>年份</label>
          <input style="max-width:120px" name="year" value="{{ year }}" />
          <label>月份</label>
          <input style="max-width:80px" name="month" value="{{ month }}" />
          <button class="btn" type="submit">查看</button>
        </form>
        <p class="muted" style="margin-top:8px">显示「本月积分」与「累计总积分」。点击用户名可查看个人明细。</p>
      </div>
      <div class="card">
        <div class="row"><div class="kpi">进行中活动</div><span class="pill">{{ events|length }} 个</span></div>
        <ul>
          {% for e in events %}
            <li>{{ e.name }} — 权重 {{ e.weight }} {% if e.is_reproduction %}(复现){% endif %}</li>
          {% else %}
            <li class="muted">暂无活动</li>
          {% endfor %}
        </ul>
      </div>
    </div>

    <div class="grid">
      <div class="card">
        <h3>主队排行榜</h3>
        <table>
          <thead><tr><th>#</th><th>队员</th><th>本月积分</th><th>累计总积分</th></tr></thead>
          <tbody>
          {% for row in main_rows %}
            <tr>
              <td>{{ loop.index }}</td>
              <td><a href="/user/{{ row.user_id }}?year={{ year }}&month={{ month }}">{{ row.username }}</a></td>
              <td>{{ '%.2f' % row.month_points }}</td>
              <td>{{ '%.2f' % row.total_points }}</td>
            </tr>
          {% else %}
            <tr><td colspan="4" class="muted">暂无数据</td></tr>
          {% endfor %}
          </tbody>
        </table>
      </div>
      <div class="card">
        <h3>子队排行榜</h3>
        <table>
          <thead><tr><th>#</th><th>队员</th><th>本月积分</th><th>累计总积分</th></tr></thead>
          <tbody>
          {% for row in sub_rows %}
            <tr>
              <td>{{ loop.index }}</td>
              <td><a href="/user/{{ row.user_id }}?year={{ year }}&month={{ month }}">{{ row.username }}</a></td>
              <td>{{ '%.2f' % row.month_points }}</td>
              <td>{{ '%.2f' % row.total_points }}</td>
            </tr>
          {% else %}
            <tr><td colspan="4" class="muted">暂无数据</td></tr>
          {% endfor %}
          </tbody>
        </table>
      </div>
    </div>
    {% endblock %}
    """,
    "rules.html": r"""
    {% extends 'base.html' %}
    {% block content %}
    <div class="card">
      <h2>战队管理规则（所有成员可见）</h2>
      <p class="muted">以下规则适用于 CloudEver 战队全体成员，违者按情节处理，严重者直接清退。</p>
      <h3>1. 战队架构</h3>
      <ul>
        <li>战队分为 <b>主队（CloudEver）</b> 与 <b>子队（Mini-CloudEver）</b>。</li>
        <li><b>子队</b>：预备力量，需定期参赛并维护技术博客，沉淀学习与成长。</li>
        <li><b>主队</b>：核心力量，冲击重要赛事；可在官网更新个人资料（联系 ENOCH）。</li>
      </ul>

      <h3>2. 招新规则</h3>
      <ul>
        <li>每年定期举办面向大一的招新赛；其他年级可参加但标准更高。</li>
        <li>赛后有意向者提交报名并参与面试。</li>
        <li>管理组综合比赛表现、潜力与品行，决定分配至主队或子队。</li>
      </ul>

      <h3>3. 队伍管理</h3>
      <ul>
        <li>子队成员展现出足够实力与协作后，安排考核；通过即晋升主队。</li>
        <li>新晋主队成员应积极参赛与训练，后期可按学业/发展节奏调整。</li>
        <li>鼓励合规范围内分享思路/工具/经验；提问前请参考《提问的智慧》。</li>
      </ul>

      <h3>4. 纪律红线（零容忍）</h3>
      <ul>
        <li><b>严禁抄袭</b>、共享他队 Flag、以利益为目的的代打或向他队寻求作弊帮助，<b>尤其是通过咸鱼等平台的“py”行为</b>；一经发现，<b>直接踢出战队</b>。</li>
        <li>比赛进行中，严禁对外泄露题目、思路或 Flag 等敏感信息。</li>
        <li>严禁对外分享队伍公共账号密码、Token 等。</li>
        <li>加入联合战队时，务必与校队资料做好隔离。</li>
      </ul>

      <h3>5. 比赛奖金分配</h3>
      <ul>
        <li>每笔奖金提取 <b>10%~20%</b> 作为团队发展基金（服务器/资料/团建等），比例由核心成员按基金情况决定。</li>
        <li>其余按贡献度分配：综合题目数量、分值、关键思路/工具贡献等（以题目贡献为主）。</li>
      </ul>

      <h3>6. 品牌建设</h3>
      <ul>
        <li>未经核心成员许可，任何人不得以战队名义对外承诺或发表官方声明。</li>
        <li>需拆分多队参赛时，仅核心队可用完整队名 CloudEver，其他使用 CloudEver_2 / CE 等衍生名。</li>
        <li>鼓励加入联合战队，但不得发表不利于校队的言论或泄露校队内部信息。</li>
      </ul>

      <h3>7. 末位淘汰与晋降规则（按月）</h3>
      <ul>
        <li>实行<b>末位淘汰制</b>：当月若<b>主队</b>积分最低成员的分数 <b>低于</b> <b>子队</b>任一成员的当月积分，则建议：
          <ul>
            <li>该主队末位成员降为子队；</li>
            <li>子队当月积分高于其的成员中，<b>当月积分最高者</b>升入主队。</li>
          </ul>
        </li>
        <li>晋降以当月积分为准，不影响累计积分的留存与展示。</li>
      </ul>
    </div>

    <div class="card">
      <h3>当前月份晋降建议</h3>
      <form class="row" method="get" action="/rules">
        <label>年份</label>
        <input style="max-width:120px" name="year" value="{{ year }}" />
        <label>月份</label>
        <input style="max-width:80px" name="month" value="{{ month }}" />
        <button class="btn" type="submit">刷新</button>
      </form>
      {% if suggestion %}
        <p style="margin-top:10px">
          建议将 <b>主队</b>末位成员 <b>{{ suggestion.demote.username }}</b>（本月 {{ '%.2f' % suggestion.demote.month_points }} 分）
          降为子队；将 <b>子队</b>成员 <b>{{ suggestion.promote.username }}</b>（本月 {{ '%.2f' % suggestion.promote.month_points }} 分）
          升入主队。<span class="muted">（依据：{{ suggestion.reason }}）</span>
        </p>
      {% else %}
        <p class="muted" style="margin-top:10px">本月暂无晋降建议（可能是子队最高分不超过主队末位）。</p>
      {% endif %}
      <p class="muted">* 此处仅展示建议，不会自动变更队伍编制；如需一键执行晋降，可联系管理员在后台开关中启用。</p>
    </div>
    {% endblock %}
    """,
    "user_profile.html": r"""
    {% extends 'base.html' %}
    {% block content %}
    <div class="card">
      <h2>成员：{{ user.username }} <span class="pill">{{ '主队' if user.team_type=='main' else '子队' }}</span></h2>
      <p class="muted">{{ year }} 年 {{ month }} 月 — 本月积分：<b>{{ '%.2f' % month_points }}</b>；累计总积分：<b>{{ '%.2f' % total_points }}</b></p>
    </div>

    <div class="card">
      <h3>本月提交明细</h3>
      <table>
        <thead><tr><th>时间</th><th>活动</th><th>已计分</th><th>待审</th><th>被撤销</th><th>结算分</th><th>WP</th></tr></thead>
        <tbody>
        {% for row in details %}
          <tr>
            <td>{{ row.created_at }}</td>
            <td>{{ row.event_name }}</td>
            <td>{{ row.count_ok }}</td>
            <td>{{ row.count_pending }}</td>
            <td>{{ row.count_revoked }}</td>
            <td>{{ '%.2f' % row.points }}</td>
            <td>
              {% if row.wp_url %}<a href="{{ row.wp_url }}" target="_blank">链接</a>{% endif %}
              {% if row.sub_id %}<a style="margin-left:8px" href="/submission/{{ row.sub_id }}">详情</a>{% endif %}
              {% if (not row.wp_url) and (not row.sub_id) %}<span class="muted">—</span>{% endif %}
            </td>
          </tr>
        {% else %}
          <tr><td colspan="7" class="muted">本月暂无提交</td></tr>
        {% endfor %}
        </tbody>
      </table>
    </div>
    {% endblock %}
    """,
    "submit_list.html": r"""
    {% extends 'base.html' %}
    {% block content %}
    <div class="card">
      <h2>提交成绩</h2>
      <p class="muted">提交后将进入管理员审核，通过后才计分。</p>
      <ul>
        {% for e in events %}
          <li>
            <a href="/submit/{{ e.id }}">{{ e.name }}</a>
            <span class="muted"> — 权重 {{ e.weight }}{% if e.is_reproduction %}，复现{% endif %}</span>
          </li>
        {% else %}
          <li class="muted">暂无可提交的活动</li>
        {% endfor %}
      </ul>
    </div>
    {% endblock %}
    """,
    "submit_event.html": r"""
    {% extends 'base.html' %}
    {% block content %}
    <div class="card">
      <h2>提交成绩 — {{ event.name }}</h2>
      <form method="post">
        <label>已解题目（由管理员预设）</label>
        {% for ch in challenges %}
          <div>
            <label>
              <input type="checkbox" name="ch_{{ ch.id }}" />
              [{{ ch.category }}] {{ ch.name }} — 基础分 {{ ch.base_score }}
            </label>
          </div>
        {% else %}
          <p class="muted">该活动尚未配置题目</p>
        {% endfor %}

        <label style="margin-top:12px">WP 链接（可选）</label>
        <input name="wp_url" placeholder="https://..." />

        <label style="margin-top:12px">WP Markdown（可选）</label>
        <textarea name="wp_md" rows="8" placeholder="# 我的解题报告
..."></textarea>

        <div style="margin-top:12px"><button class="btn" type="submit">提交</button></div>
      </form>
    </div>
    {% endblock %}
    """,

    "admin_events.html": r"""
    {% extends 'base.html' %}
    {% block content %}
    <div class="card">
      <h2>活动管理</h2>
      <table>
        <thead><tr><th>名称</th><th>权重</th><th>类型</th><th>时间</th><th></th></tr></thead>
        <tbody>
        {% for e in events %}
          <tr>
            <td>{{ e.name }}</td>
            <td>{{ e.weight }}</td>
            <td>{% if e.is_reproduction %}复现{% else %}CTF{% endif %}</td>
            <td>{{ e.start_time }} → {{ e.end_time }}</td>
            <td><a href="/admin/events/{{ e.id }}/challenges">题目管理</a></td>
          </tr>
        {% else %}
          <tr><td colspan="5" class="muted">暂无活动</td></tr>
        {% endfor %}
        </tbody>
      </table>
    </div>

    <div class="card">
      <h3>新建活动</h3>
      <form method="post" action="/admin/events/create">
        <label>名称</label>
        <input name="name" required />
        <label>开始时间（YYYY-MM-DD HH:MM）</label>
        <input name="start" placeholder="2025-11-03 10:00" />
        <label>结束时间（YYYY-MM-DD HH:MM）</label>
        <input name="end" placeholder="2025-11-05 10:00" />
        <label>活动权重</label>
        <input name="weight" value="1.0" />
        <label>类型</label>
        <select name="is_reproduction">
          <option value="0">CTF</option>
          <option value="1">复现</option>
        </select>
        <div style="margin-top:12px"><button class="btn" type="submit">创建</button></div>
      </form>
    </div>

    <div class="card">
      <h3>按月清空提交</h3>
      <form method="post" action="/admin/reset_month" class="row">
        <label>年份</label><input style="max-width:120px" name="year" value="{{ year }}" />
        <label>月份</label><input style="max-width:80px" name="month" value="{{ month }}" />
        <button class="btn warn" type="submit">清空该月</button>
      </form>
      <p class="muted">此操作将删除该月份内的所有提交（不可恢复）。</p>
    </div>
    {% endblock %}
    """,
    "admin_challenges.html": r"""
    {% extends 'base.html' %}
    {% block content %}
    <div class="card">
      <h2>题目管理 — {{ event.name }}</h2>
      <table>
        <thead><tr><th>题目名</th><th>类别</th><th>基础分</th><th>操作</th></tr></thead>
        <tbody>
        {% for ch in challenges %}
          <tr>
            <td>{{ ch.name }}</td>
            <td>{{ ch.category }}</td>
            <td>{{ ch.base_score }}</td>
            <td>
              <form method="post" action="/admin/events/{{ event.id }}/challenges/{{ ch.id }}/delete" style="display:inline" onsubmit="return confirm('确定删除【{{ ch.name }}】？此操作不可恢复');">
                <button class="btn warn" type="submit">删除</button>
              </form>
            </td>
          </tr>
        {% else %}
          <tr><td colspan="4" class="muted">暂无题目</td></tr>
        {% endfor %}
        </tbody>
      </table>
    </div>

    <div class="card">
      <h3>新增题目</h3>
      <form method="post" action="/admin/events/{{ event.id }}/challenges/add">
        <label>题目名</label>
        <input name="name" required />
        <label>类别</label>
        <select name="category">
          {% for c in categories %}<option>{{ c }}</option>{% endfor %}
        </select>
        <label>基础分</label>
        <input name="base_score" value="100" />
        <div style="margin-top:12px"><button class="btn" type="submit">添加</button></div>
      </form>
    </div>
    {% endblock %}
    """,

    "admin_users.html": r"""
    {% extends 'base.html' %}
    {% block content %}
    <div class="card">
      <h2>成员管理</h2>
      <table>
        <thead><tr><th>用户名</th><th>角色</th><th>队伍</th><th>操作</th></tr></thead>
        <tbody>
        {% for u in users %}
          <tr>
            <td>{{ u.username }}</td>
            <td>{{ u.role }}</td>
            <td>{{ u.team_type }}</td>
            <td>
              <form method="post" action="/admin/users/{{ u.id }}/update" class="row">
                <select name="role">
                  <option value="member" {% if u.role=='member' %}selected{% endif %}>member</option>
                  <option value="admin" {% if u.role=='admin' %}selected{% endif %}>admin</option>
                </select>
                <select name="team_type">
                  <option value="main" {% if u.team_type=='main' %}selected{% endif %}>main(主队)</option>
                  <option value="sub" {% if u.team_type=='sub' %}selected{% endif %}>sub(子队)</option>
                </select>
                <button class="btn secondary" type="submit">保存</button>
              </form>
            </td>
          </tr>
        {% else %}
          <tr><td colspan="4" class="muted">暂无成员</td></tr>
        {% endfor %}
        </tbody>
      </table>
    </div>
    {% endblock %}
    """,
    "admin_review.html": r"""
    {% extends 'base.html' %}
    {% block content %}
    <div class="card">
      <h2>审核中心</h2>
      <p class="muted">显示所有提交记录（含已审核），可进入复审并切换通过/撤销状态。</p>
      <table>
        <thead><tr><th>时间</th><th>成员</th><th>活动</th><th>待审</th><th>已计分</th><th>被撤销</th><th></th></tr></thead>
        <tbody>
        {% for r in rows %}
          <tr>
            <td>{{ r.created_at }}</td>
            <td>{{ r.username }}</td>
            <td>{{ r.event_name }}</td>
            <td>{{ r.pending }}</td>
            <td>{{ r.ok }}</td>
            <td>{{ r.rev }}</td>
            <td><a class="btn secondary" href="/admin/review/{{ r.sub_id }}">查看</a></td>
          </tr>
        {% else %}
          <tr><td colspan="7" class="muted">暂无提交</td></tr>
        {% endfor %}
        </tbody>
      </table>
    </div>
    {% endblock %}
    """,
    "admin_review_detail.html": r"""
    {% extends 'base.html' %}
    {% block content %}
    <div class="card">
      <h2>审核 — 提交 #{{ sub.id }} · {{ user.username }} · {{ event.name }}</h2>
      <p class="muted">
        提交时间：{{ sub.created_at }}
        {% if sub.wp_url %}｜外链：<a href="{{ sub.wp_url }}" target="_blank">{{ sub.wp_url }}</a>{% endif %}
        {% if sub.wp_md %}｜<a href="/submission/{{ sub.id }}">查看 Markdown</a>{% endif %}
      </p>
      <form method="post" action="/admin/review/{{ sub.id }}/approve_all">
        <button class="btn" type="submit">一键通过所有待审题目</button>
      </form>
    </div>

    <div class="card">
      <h3>题目列表</h3>
      <table>
        <thead><tr><th>#</th><th>类别</th><th>题目</th><th>基础分</th><th>状态</th><th>操作</th></tr></thead>
        <tbody>
        {% for it in items %}
          <tr>
            <td>{{ loop.index }}</td>
            <td>{{ it.challenge.category }}</td>
            <td>{{ it.challenge.name }}</td>
            <td>{{ it.challenge.base_score }}</td>
            <td>
              {% if not it.approved %}<span class="status pending">待审</span>
              {% elif it.revoked %}<span class="status rev">已撤销</span>
              {% else %}<span class="status ok">已计分</span>{% endif %}
            </td>
            <td class="row">
              <form method="post" action="/admin/review/item/{{ it.id }}/toggle_approve">
                <button class="btn secondary" type="submit">{{ '取消通过' if it.approved else '通过' }}</button>
              </form>
              <form method="post" action="/admin/review/item/{{ it.id }}/toggle_revoke">
                <button class="btn warn" type="submit">{{ '恢复分数' if it.revoked else '撤销分数' }}</button>
              </form>
            </td>
          </tr>
        {% else %}
          <tr><td colspan="6" class="muted">该提交暂无题目</td></tr>
        {% endfor %}
        </tbody>
      </table>
    </div>
    {% endblock %}
    """,
    "submission_detail.html": r"""
    {% extends 'base.html' %}
    {% block content %}
    <div class="card">
      <h2>提交详情 #{{ sub.id }} · {{ user.username }} · {{ event.name }}</h2>
      <p class="muted">
        提交时间：{{ sub.created_at }}
        {% if sub.wp_url %}｜外链：<a href="{{ sub.wp_url }}" target="_blank">{{ sub.wp_url }}</a>{% endif %}
      </p>
    </div>

    {% if wp_html %}
    <div class="card">
      <h3>解题报告（Markdown 渲染）</h3>
      <div class="md">{{ wp_html|safe }}</div>
    </div>
    {% endif %}

    <div class="card">
      <h3>题目条目</h3>
      <table>
        <thead><tr><th>#</th><th>类别</th><th>题目</th><th>基础分</th><th>状态</th></tr></thead>
        <tbody>
        {% for it in items %}
          <tr>
            <td>{{ loop.index }}</td>
            <td>{{ it.challenge.category }}</td>
            <td>{{ it.challenge.name }}</td>
            <td>{{ it.challenge.base_score }}</td>
            <td>
              {% if not it.approved %}<span class="status pending">待审</span>
              {% elif it.revoked %}<span class="status rev">已撤销</span>
              {% else %}<span class="status ok">已计分</span>{% endif %}
            </td>
          </tr>
        {% else %}
          <tr><td colspan="5" class="muted">无</td></tr>
        {% endfor %}
        </tbody>
      </table>
    </div>
    {% endblock %}
    """,
}

jinja_env = Environment(loader=DictLoader(TEMPLATES), autoescape=select_autoescape(['html']))

def render_template(name: str, **ctx) -> HTMLResponse:
    html = jinja_env.get_template(name).render(**ctx)
    return HTMLResponse(html)

# ---------------------------------------------------
# 工具函数
# ---------------------------------------------------

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def get_current_user(request: Request, db: Session = Depends(get_db)) -> Optional[User]:
    uid = request.session.get("user_id")
    return db.get(User, uid) if uid else None


def require_login(user: Optional[User]):
    if not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="需要先登录")


def require_admin(user: Optional[User]):
    require_login(user)
    if user.role != "admin":
        raise HTTPException(status_code=403, detail="需要管理员权限")


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
    """最终得分 = Σ(通过且未撤销的题目基础分) × 活动权重"""
    if not sub.event:
        return 0.0
    total = 0.0
    for it in sub.items:
        ch = it.challenge
        if ch and it.approved and not it.revoked:
            total += float(ch.base_score)
    return total * float(sub.event.weight or 1.0)


def leaderboard_month_and_total(db: Session, year: int, month: int, team_type: str) -> List[Dict[str, float]]:
    start, end = month_range(year, month)

    subs_month = (
        db.query(Submission)
        .join(User, Submission.user_id == User.id)
        .filter(User.team_type == team_type)
        .filter(Submission.created_at >= start, Submission.created_at < end)
        .all()
    )
    subs_total = (
        db.query(Submission)
        .join(User, Submission.user_id == User.id)
        .filter(User.team_type == team_type)
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


def md_to_html(md_text: Optional[str]) -> str:
    if not md_text:
        return ""
    # 基础扩展：表格/围栏代码；可按需添加 codehilite 等
    return mdlib.markdown(md_text, extensions=["fenced_code", "tables"]) or ""

# ---------------------------------------------------
# 认证 + 注册
# ---------------------------------------------------
@app.get("/auth/login", response_class=HTMLResponse)
def login_page(request: Request, current_user: Optional[User] = Depends(get_current_user)):
    if current_user:
        return RedirectResponse("/", status_code=302)
    return render_template("login.html", title="登录", current_user=None, msg=request.query_params.get("msg"))


@app.post("/auth/login")
def do_login(request: Request, username: str = Form(...), password: str = Form(...), db: Session = Depends(get_db)):
    user = db.query(User).filter(User.username == username).first()
    if not user or not user.check_password(password):
        return RedirectResponse("/auth/login?msg=账号或密码错误", status_code=302)
    request.session["user_id"] = user.id
    return RedirectResponse("/", status_code=302)


@app.post("/auth/logout")
def do_logout(request: Request):
    request.session.clear()
    return RedirectResponse("/auth/login?msg=已退出登录", status_code=302)


@app.get("/auth/register", response_class=HTMLResponse)
def register_page(request: Request, current_user: Optional[User] = Depends(get_current_user)):
    if current_user:
        return RedirectResponse("/", status_code=302)
    return render_template("register.html", title="注册", current_user=None, msg=request.query_params.get("msg"))


@app.post("/auth/register")
def do_register(request: Request, username: str = Form(...), password: str = Form(...), password2: str = Form(...), db: Session = Depends(get_db)):
    if password != password2:
        return RedirectResponse("/auth/register?msg=两次密码不一致", status_code=302)
    if db.query(User).filter(User.username == username).first():
        return RedirectResponse("/auth/register?msg=用户名已存在", status_code=302)
    u = User(username=username.strip(), password_hash=pwdhash.hash(password), role="member", team_type="sub")
    db.add(u); db.commit()
    return RedirectResponse("/auth/login?msg=注册成功, 请登录", status_code=302)

# ---------------------------------------------------
# 首页：积分榜 + 活动概览
# ---------------------------------------------------
@app.get("/", response_class=HTMLResponse)
def index(request: Request, year: Optional[int] = None, month: Optional[int] = None, db: Session = Depends(get_db), current_user: Optional[User] = Depends(get_current_user)):
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

# ---------------------------------------------------
# 规则公开页：所有成员可见 + 当月晋降建议
# ---------------------------------------------------
@app.get("/rules", response_class=HTMLResponse)
def rules_page(request: Request, year: Optional[int] = None, month: Optional[int] = None, db: Session = Depends(get_db), current_user: Optional[User] = Depends(get_current_user)):
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

# ---------------------------------------------------
# 成绩提交（队员）
# ---------------------------------------------------
@app.get("/submit", response_class=HTMLResponse)
def submit_list(request: Request, db: Session = Depends(get_db), current_user: Optional[User] = Depends(get_current_user)):
    require_login(current_user)
    events = db.query(Event).filter(Event.is_active == True).all()
    return render_template("submit_list.html", title="提交成绩", current_user=current_user, events=events)


@app.get("/submit/{event_id}", response_class=HTMLResponse)
def submit_event_page(event_id: int, request: Request, db: Session = Depends(get_db), current_user: Optional[User] = Depends(get_current_user)):
    require_login(current_user)
    event = db.get(Event, event_id)
    if not event or not event.is_active:
        raise HTTPException(404, "活动不存在或未启用")
    challenges = db.query(Challenge).filter(Challenge.event_id == event_id).all()
    return render_template("submit_event.html", title=f"提交 {event.name}", current_user=current_user, event=event, challenges=challenges)


@app.post("/submit/{event_id}")
async def submit_event_action(event_id: int, request: Request, db: Session = Depends(get_db), current_user: Optional[User] = Depends(get_current_user)):
    require_login(current_user)
    event = db.get(Event, event_id)
    if not event or not event.is_active:
        raise HTTPException(404, "活动不存在或未启用")

    form = await await_form(request)
    wp_url = form.get("wp_url") or None
    wp_md = form.get("wp_md") or None

    sub = Submission(user_id=current_user.id, event_id=event_id, wp_url=wp_url, wp_md=wp_md)
    db.add(sub); db.flush()

    # 勾选题目 → 创建待审条目
    for ch in db.query(Challenge).filter(Challenge.event_id == event_id).all():
        if form.get(f"ch_{ch.id}") is not None:
            db.add(SubmissionItem(submission_id=sub.id, challenge_id=ch.id, approved=False, revoked=False))

    db.commit()
    return RedirectResponse("/submit?msg=提交成功，等待管理员审核后计分", status_code=302)

# ---------------------------------------------------
# 管理区：审核中心 / 活动 / 题目 / 成员 / 按月清空
# ---------------------------------------------------
@app.get("/admin/review", response_class=HTMLResponse)
def admin_review_list(request: Request, db: Session = Depends(get_db), current_user: Optional[User] = Depends(get_current_user)):
    require_admin(current_user)
    subs = db.query(Submission).all()
    rows = []
    for s in subs:
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
    return render_template("admin_review.html", title="审核中心", current_user=current_user, rows=rows)


@app.get("/admin/review/{sub_id}", response_class=HTMLResponse)
def admin_review_detail(sub_id: int, request: Request, db: Session = Depends(get_db), current_user: Optional[User] = Depends(get_current_user)):
    require_admin(current_user)
    sub = db.get(Submission, sub_id)
    if not sub:
        raise HTTPException(404, "提交不存在")
    items = db.query(SubmissionItem).filter(SubmissionItem.submission_id == sub_id).all()
    return render_template("admin_review_detail.html", title="审核提交", current_user=current_user, sub=sub, user=sub.user, event=sub.event, items=items)


@app.post("/admin/review/{sub_id}/approve_all")
def admin_review_approve_all(sub_id: int, db: Session = Depends(get_db), current_user: Optional[User] = Depends(get_current_user)):
    require_admin(current_user)
    items = db.query(SubmissionItem).filter(SubmissionItem.submission_id == sub_id).all()
    for it in items:
        if not it.approved:
            it.approved = True
    db.commit()
    return RedirectResponse(f"/admin/review/{sub_id}?msg=全部通过", status_code=302)


@app.post("/admin/review/item/{item_id}/toggle_approve")
def admin_toggle_approve(item_id: int, db: Session = Depends(get_db), current_user: Optional[User] = Depends(get_current_user)):
    require_admin(current_user)
    it = db.get(SubmissionItem, item_id)
    if not it:
        raise HTTPException(404, "条目不存在")
    it.approved = not it.approved
    if not it.approved:
        it.revoked = False
    db.commit()
    return RedirectResponse(f"/admin/review/{it.submission_id}?msg=已切换通过状态", status_code=302)


@app.post("/admin/review/item/{item_id}/toggle_revoke")
def admin_toggle_revoke(item_id: int, db: Session = Depends(get_db), current_user: Optional[User] = Depends(get_current_user)):
    require_admin(current_user)
    it = db.get(SubmissionItem, item_id)
    if not it:
        raise HTTPException(404, "条目不存在")
    if it.approved:
        it.revoked = not it.revoked
    db.commit()
    return RedirectResponse(f"/admin/review/{it.submission_id}?msg=已切换撤销状态", status_code=302)


@app.get("/admin/events", response_class=HTMLResponse)
def admin_events(request: Request, db: Session = Depends(get_db), current_user: Optional[User] = Depends(get_current_user)):
    require_admin(current_user)
    events = db.query(Event).all()
    now = datetime.now(TZ)
    return render_template("admin_events.html", title="活动管理", current_user=current_user, events=events, year=now.year, month=now.month)


@app.post("/admin/events/create")
def admin_create_event(request: Request, name: str = Form(...), start: str = Form(""), end: str = Form(""), weight: float = Form(1.0), is_reproduction: int = Form(0), db: Session = Depends(get_db), current_user: Optional[User] = Depends(get_current_user)):
    require_admin(current_user)

    def parse_dt(s: str) -> Optional[datetime]:
        s = (s or "").strip()
        if not s:
            return None
        try:
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


@app.get("/admin/events/{event_id}/challenges", response_class=HTMLResponse)
def admin_event_challenges(event_id: int, request: Request, db: Session = Depends(get_db), current_user: Optional[User] = Depends(get_current_user)):
    require_admin(current_user)
    event = db.get(Event, event_id)
    if not event:
        raise HTTPException(404, "活动不存在")
    challenges = db.query(Challenge).filter(Challenge.event_id == event_id).all()
    return render_template("admin_challenges.html", title="题目管理", current_user=current_user, event=event, challenges=challenges, categories=CATEGORIES, msg=request.query_params.get("msg"))


@app.post("/admin/events/{event_id}/challenges/add")
def admin_add_challenge(event_id: int, name: str = Form(...), category: str = Form("misc"), base_score: int = Form(100), db: Session = Depends(get_db), current_user: Optional[User] = Depends(get_current_user)):
    require_admin(current_user)
    event = db.get(Event, event_id)
    if not event:
        raise HTTPException(404, "活动不存在")
    ch = Challenge(event_id=event_id, name=name.strip(), category=category.strip(), base_score=int(base_score))
    db.add(ch)
    db.commit()
    return RedirectResponse(f"/admin/events/{event_id}/challenges?msg=已添加", status_code=302)


@app.post("/admin/events/{event_id}/challenges/{ch_id}/delete")
def admin_delete_challenge(event_id: int, ch_id: int, db: Session = Depends(get_db), current_user: Optional[User] = Depends(get_current_user)):
    require_admin(current_user)
    event = db.get(Event, event_id)
    if not event:
        raise HTTPException(404, "活动不存在")
    ch = db.get(Challenge, ch_id)
    if not ch or ch.event_id != event_id:
        raise HTTPException(404, "题目不存在")

    # 若已有提交引用该题目，则不允许删除，避免破坏历史记录
    refcount = db.query(SubmissionItem).filter(SubmissionItem.challenge_id == ch_id).count()
    if refcount > 0:
        return RedirectResponse(f"/admin/events/{event_id}/challenges?msg=该题目已被{refcount}条提交引用，不能删除", status_code=302)

    db.delete(ch)
    db.commit()
    return RedirectResponse(f"/admin/events/{event_id}/challenges?msg=已删除", status_code=302)


@app.get("/admin/users", response_class=HTMLResponse)
def admin_users(request: Request, db: Session = Depends(get_db), current_user: Optional[User] = Depends(get_current_user)):
    require_admin(current_user)
    users = db.query(User).order_by(User.username.asc()).all()
    return render_template("admin_users.html", title="成员管理", current_user=current_user, users=users)


@app.post("/admin/users/{uid}/update")
def admin_update_user(uid: int, role: str = Form(...), team_type: str = Form(...), db: Session = Depends(get_db), current_user: Optional[User] = Depends(get_current_user)):
    require_admin(current_user)
    u = db.get(User, uid)
    if not u:
        raise HTTPException(404, "用户不存在")
    u.role = role if role in ("member", "admin") else u.role
    u.team_type = team_type if team_type in ("main", "sub") else u.team_type
    db.commit()
    return RedirectResponse("/admin/users?msg=已更新", status_code=302)


@app.post("/admin/reset_month")
def admin_reset_month(year: int = Form(...), month: int = Form(...), db: Session = Depends(get_db), current_user: Optional[User] = Depends(get_current_user)):
    require_admin(current_user)
    start = datetime(int(year), int(month), 1, tzinfo=TZ)
    end = datetime(int(year) + (1 if int(month) == 12 else 0), 1 if int(month) == 12 else int(month) + 1, 1, tzinfo=TZ)
    q = db.query(Submission).filter(Submission.created_at >= start, Submission.created_at < end)
    count = q.count()
    for s in q.all():
        db.delete(s)
    db.commit()
    return RedirectResponse(f"/admin/events?msg=已清空{year}-{month}提交({count}条)", status_code=302)

# ---------------------------------------------------
# 提交详情（Markdown 渲染）
# ---------------------------------------------------
@app.get("/submission/{sub_id}", response_class=HTMLResponse)
def submission_detail(sub_id: int, request: Request, db: Session = Depends(get_db), current_user: Optional[User] = Depends(get_current_user)):
    sub = db.get(Submission, sub_id)
    if not sub:
        raise HTTPException(404, "提交不存在")
    items = db.query(SubmissionItem).filter(SubmissionItem.submission_id == sub_id).all()
    wp_html = md_to_html(sub.wp_md)  # 若存在 Markdown 内容则渲染；即使同时有外链也显示内容
    return render_template("submission_detail.html", title="提交详情", current_user=current_user, sub=sub, user=sub.user, event=sub.event, items=items, wp_html=wp_html)

# ---------------------------------------------------
# 成员个人页
# ---------------------------------------------------
@app.get("/user/{uid}", response_class=HTMLResponse)
def user_profile(uid: int, request: Request, year: Optional[int] = None, month: Optional[int] = None, db: Session = Depends(get_db), current_user: Optional[User] = Depends(get_current_user)):
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

    def sum_points(subs: List[Submission]) -> float:
        return sum(compute_submission_points(s) for s in subs)

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

# ---------------------------------------------------
# 辅助：表单读取
# ---------------------------------------------------
async def await_form(request: Request) -> Dict[str, str]:
    form = await request.form()
    return {k: (v if isinstance(v, str) else v.filename if hasattr(v, 'filename') else str(v)) for k, v in form.items()}

# ---------------------------------------------------
# 初始化：建表、默认管理员
# ---------------------------------------------------
Base.metadata.create_all(bind=engine)

with SessionLocal() as db:
    if db.query(User).count() == 0:
        db.add(User(
            username="admin",
            password_hash=pwdhash.hash("1qaz@WSX"),  # 默认管理员密码 1qaz@WSX
            role="admin",
            team_type="main",
        ))
        db.commit()

# 运行：uvicorn app:app --reload --port 52123 --host 127.0.0.1
