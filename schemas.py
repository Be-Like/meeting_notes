from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import List, Optional

from pydantic import BaseModel, EmailStr


# User schemas
class UserBase(BaseModel):
    name: str
    email: EmailStr


class UserCreate(UserBase):
    pass


class User(UserBase):
    id: int

    class Config:
        from_attributes = True


# Meeting schemas
class MeetingBase(BaseModel):
    title: str
    description: Optional[str] = None
    scheduled_time: datetime


class MeetingCreate(MeetingBase):
    attendee_ids: List[int] = []


class MeetingUpdate(BaseModel):
    title: Optional[str] = None
    description: Optional[str] = None
    scheduled_time: Optional[datetime] = None
    attendee_ids: Optional[List[int]] = None


class Meeting(MeetingBase):
    id: int
    created_at: datetime
    attendees: List[User] = []

    class Config:
        from_attributes = True


# Note schemas
class NoteBase(BaseModel):
    content: str


class NoteCreate(NoteBase):
    meeting_id: int


class Note(NoteBase):
    id: int
    meeting_id: int
    created_at: datetime

    class Config:
        from_attributes = True


# Task schemas
class TaskStatus(str, Enum):
    pending = "pending"
    completed = "completed"
    cancelled = "cancelled"


class TaskBase(BaseModel):
    title: str
    description: Optional[str] = None
    status: TaskStatus = TaskStatus.pending


class TaskCreate(TaskBase):
    due_meeting_id: int


class TaskUpdate(BaseModel):
    title: Optional[str] = None
    description: Optional[str] = None
    status: Optional[TaskStatus] = None
    due_meeting_id: Optional[int] = None


class Task(TaskBase):
    id: int
    due_meeting_id: int
    created_at: datetime

    class Config:
        from_attributes = True


# MeetingWithDetails must be defined after Note and Task
class MeetingWithDetails(Meeting):
    notes: List[Note] = []
    tasks: List[Task] = []

    class Config:
        from_attributes = True
