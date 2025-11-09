from datetime import datetime
from sqlalchemy import Column, DateTime, Float, ForeignKey, Integer, String, Text, Boolean
from sqlalchemy.orm import relationship

from .database import Base
from .config import TZ

class Setting(Base):
    __tablename__ = "settings"
    key = Column(String, primary_key=True)
    value = Column(Text, nullable=False)


class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True)
    username = Column(String, unique=True, nullable=False)
    password_hash = Column(String, nullable=False)
    role = Column(String, default="member")  # 'admin' | 'reviewer' | 'member'
    team_type = Column(String, default="sub")  # 'main' or 'sub'
    is_active = Column(Boolean, default=True)
    is_deleted = Column(Boolean, default=False)
    avatar_filename = Column(String, nullable=True)
    # disambiguate: Submission has two FKs to users (user_id, rejected_by_id)
    submissions = relationship("Submission", back_populates="user", foreign_keys="Submission.user_id")


class Event(Base):
    __tablename__ = "events"
    id = Column(Integer, primary_key=True)
    name = Column(String, nullable=False)
    start_time = Column(DateTime(timezone=True))
    end_time = Column(DateTime(timezone=True))
    weight = Column(Float, default=1.0)
    is_reproduction = Column(Boolean, default=False)
    is_active = Column(Boolean, default=True)
    allow_wp_only = Column(Boolean, default=False)  # 允许仅提交WP，由管理员在审核时手动给分
    is_deleted = Column(Boolean, default=False)
    event_type_id = Column(Integer, ForeignKey("event_types.id"), nullable=True)

    challenges = relationship("Challenge", back_populates="event", cascade="all,delete")
    submissions = relationship("Submission", back_populates="event")
    event_type = relationship("EventType")


class Challenge(Base):
    __tablename__ = "challenges"
    id = Column(Integer, primary_key=True)
    event_id = Column(Integer, ForeignKey("events.id"), nullable=False)
    name = Column(String, nullable=False)
    category = Column(String, default="misc")
    base_score = Column(Integer, default=100)
    is_deleted = Column(Boolean, default=False)

    event = relationship("Event", back_populates="challenges")


class Submission(Base):
    __tablename__ = "submissions"
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    event_id = Column(Integer, ForeignKey("events.id"), nullable=False)
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(TZ))

    wp_url = Column(String, nullable=True)
    wp_md = Column(Text, nullable=True)
    manual_points = Column(Float, nullable=True)  # 管理员在审核时手动设定的总分（优先级高于题目累计）
    is_deleted = Column(Boolean, default=False)
    # 驳回（打回）相关字段：被管理员或审核员整条退回，需成员重新编辑提交
    rejected = Column(Boolean, default=False)
    rejected_reason = Column(Text, nullable=True)
    rejected_at = Column(DateTime(timezone=True), nullable=True)
    rejected_by_id = Column(Integer, ForeignKey("users.id"), nullable=True)

    #明确关联到提交者
    user = relationship("User", back_populates="submissions", foreign_keys=[user_id])
    #关联到驳回操作者（只读）
    rejected_by = relationship("User", foreign_keys=[rejected_by_id])
    event = relationship("Event", back_populates="submissions")
    items = relationship("SubmissionItem", back_populates="submission", cascade="all,delete-orphan")


class SubmissionItem(Base):
    __tablename__ = "submission_items"
    id = Column(Integer, primary_key=True)
    submission_id = Column(Integer, ForeignKey("submissions.id"), nullable=False)
    challenge_id = Column(Integer, ForeignKey("challenges.id"), nullable=False)

    approved = Column(Boolean, default=False)
    revoked = Column(Boolean, default=False)
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(TZ))

    submission = relationship("Submission", back_populates="items")
    challenge = relationship("Challenge")


class Announcement(Base):
    __tablename__ = "announcements"
    id = Column(Integer, primary_key=True)
    title = Column(String, nullable=False)
    content = Column(Text, nullable=False)
    visible = Column(Boolean, default=True)
    is_deleted = Column(Boolean, default=False)
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(TZ))
    updated_at = Column(DateTime(timezone=True), default=lambda: datetime.now(TZ))


class PointAdjustment(Base):
    __tablename__ = "point_adjustments"
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    year = Column(Integer, nullable=False)
    month = Column(Integer, nullable=False)
    amount = Column(Float, nullable=False, default=0.0)  # 正负分均可
    reason = Column(Text, nullable=True)
    is_deleted = Column(Boolean, default=False)
    created_by_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(TZ))


class EventType(Base):
    __tablename__ = "event_types"
    id = Column(Integer, primary_key=True)
    name = Column(String, nullable=False, unique=True)
    description = Column(Text, nullable=True)
    is_active = Column(Boolean, default=True)
    is_deleted = Column(Boolean, default=False)
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(TZ))
    updated_at = Column(DateTime(timezone=True), default=lambda: datetime.now(TZ))


class Notification(Base):
    """针对用户的站内通知，例如提交被驳回等。"""
    __tablename__ = "notifications"
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    type = Column(String, nullable=False)  # 'rejection' | 其他预留
    title = Column(String, nullable=True)
    content = Column(Text, nullable=False)
    related_id = Column(Integer, nullable=True)  # 关联的实体，如 submission.id
    batch_id = Column(String, nullable=True)  # 同一次发布的分组ID，支持聚合显示与批量操作
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(TZ))
    read_at = Column(DateTime(timezone=True), nullable=True)
    is_deleted = Column(Boolean, default=False)
