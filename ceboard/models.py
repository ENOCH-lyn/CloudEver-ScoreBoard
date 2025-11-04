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
    role = Column(String, default="member")  # 'admin' or 'member'
    team_type = Column(String, default="sub")  # 'main' or 'sub'
    is_active = Column(Boolean, default=True)
    avatar_filename = Column(String, nullable=True)

    submissions = relationship("Submission", back_populates="user")


class Event(Base):
    __tablename__ = "events"
    id = Column(Integer, primary_key=True)
    name = Column(String, nullable=False)
    start_time = Column(DateTime(timezone=True))
    end_time = Column(DateTime(timezone=True))
    weight = Column(Float, default=1.0)
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

    wp_url = Column(String, nullable=True)
    wp_md = Column(Text, nullable=True)

    user = relationship("User", back_populates="submissions")
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
