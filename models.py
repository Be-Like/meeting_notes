from datetime import datetime, timezone

from sqlalchemy import Column, DateTime, ForeignKey, Integer, String, Table, Text
from sqlalchemy.orm import declarative_base, relationship
from sqlalchemy.types import TypeDecorator

Base = declarative_base()


class UTCDateTime(TypeDecorator):
    """DateTime that is always timezone-aware UTC in Python, whatever the database stores."""

    impl = DateTime(timezone=True)
    cache_ok = True

    def process_bind_param(self, value, dialect):
        # Naive values are taken to be UTC; aware values are converted to UTC.
        if value is None:
            return None
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)

    def process_result_value(self, value, dialect):
        # SQLite stores no offset and returns naive values, so UTC is attached on the way out.
        if value is None:
            return None
        return value.replace(tzinfo=timezone.utc)


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


# Association table for many-to-many relationship between meetings and users
meeting_users = Table(
    "meeting_users",
    Base.metadata,
    Column("meeting_id", Integer, ForeignKey("meetings.id"), primary_key=True),
    Column("user_id", Integer, ForeignKey("users.id"), primary_key=True),
)


class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, nullable=False)
    email = Column(String, unique=True, nullable=False, index=True)

    # Relationships
    meetings = relationship("Meeting", secondary=meeting_users, back_populates="attendees")


class Meeting(Base):
    __tablename__ = "meetings"

    id = Column(Integer, primary_key=True, index=True)
    title = Column(String, nullable=False)
    description = Column(Text)
    scheduled_time = Column(UTCDateTime, nullable=False)
    created_at = Column(UTCDateTime, default=utc_now)

    # Relationships
    attendees = relationship("User", secondary=meeting_users, back_populates="meetings")
    notes = relationship("Note", back_populates="meeting", cascade="all, delete-orphan")
    tasks = relationship(
        "Task", back_populates="due_meeting", foreign_keys="Task.due_meeting_id", cascade="all, delete-orphan"
    )


class Note(Base):
    __tablename__ = "notes"

    id = Column(Integer, primary_key=True, index=True)
    content = Column(Text, nullable=False)
    created_at = Column(UTCDateTime, default=utc_now)
    meeting_id = Column(Integer, ForeignKey("meetings.id"), nullable=False)

    # Relationships
    meeting = relationship("Meeting", back_populates="notes")


class Task(Base):
    __tablename__ = "tasks"

    id = Column(Integer, primary_key=True, index=True)
    title = Column(String, nullable=False)
    description = Column(Text)
    status = Column(String, default="pending")  # pending, completed, cancelled
    created_at = Column(UTCDateTime, default=utc_now)
    due_meeting_id = Column(Integer, ForeignKey("meetings.id"), nullable=False)

    # Relationships
    due_meeting = relationship("Meeting", back_populates="tasks", foreign_keys=[due_meeting_id])
