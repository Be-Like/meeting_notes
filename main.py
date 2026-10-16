from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import List, Optional

from fastapi import FastAPI, Query

import schemas
from database import create_tables


@asynccontextmanager
async def lifespan(app: FastAPI):
    create_tables()
    yield


app = FastAPI(
    title="Meeting Notes API",
    description="API for managing meetings, notes, and tasks",
    version="1.0.0",
    lifespan=lifespan,
)


# User endpoints
@app.post("/users/", response_model=schemas.User, status_code=201)
def create_user(user: schemas.UserCreate):
    """
    Create a new user.
    """
    # Stub: Return a mock user
    return schemas.User(id=1, name=user.name, email=user.email)


@app.get("/users/", response_model=List[schemas.User])
def list_users():
    """
    List all users.
    """
    # Stub: Return empty list
    return []


@app.get("/users/{user_id}", response_model=schemas.User)
def get_user(user_id: int):
    """
    Get a specific user by ID.
    """
    # Stub: Return a mock user
    return schemas.User(id=user_id, name="John Doe", email="john@example.com")


# Meeting endpoints
@app.post("/meetings/", response_model=schemas.Meeting, status_code=201)
def create_meeting(meeting: schemas.MeetingCreate):
    """
    Create a new meeting with attendees.
    """
    # Stub: Return a mock meeting
    return schemas.Meeting(
        id=1,
        title=meeting.title,
        description=meeting.description,
        scheduled_time=meeting.scheduled_time,
        created_at=meeting.scheduled_time,
        attendees=[],
    )


@app.get("/meetings/", response_model=List[schemas.Meeting])
def list_meetings():
    """
    List all meetings.
    """
    # Stub: Return empty list
    return []


@app.get("/meetings/{meeting_id}", response_model=schemas.MeetingWithDetails)
def get_meeting(meeting_id: int):
    """
    Get a specific meeting with all details (attendees, notes, tasks).
    """
    # Stub: Return a mock meeting with details
    return schemas.MeetingWithDetails(
        id=meeting_id,
        title="Team Standup",
        description="Daily standup meeting",
        scheduled_time=datetime.now(timezone.utc),
        created_at=datetime.now(timezone.utc),
        attendees=[],
        notes=[],
        tasks=[],
    )


@app.put("/meetings/{meeting_id}", response_model=schemas.Meeting)
def update_meeting(meeting_id: int, meeting: schemas.MeetingUpdate):
    """
    Update a meeting.
    """
    # Stub: Return a mock updated meeting
    return schemas.Meeting(
        id=meeting_id,
        title=meeting.title or "Updated Meeting",
        description=meeting.description,
        scheduled_time=meeting.scheduled_time or datetime.now(timezone.utc),
        created_at=datetime.now(timezone.utc),
        attendees=[],
    )


@app.delete("/meetings/{meeting_id}", status_code=204)
def delete_meeting(meeting_id: int):
    """
    Delete a meeting.
    """
    # Stub: Just return success
    return None


# Note endpoints
@app.post("/notes/", response_model=schemas.Note, status_code=201)
def create_note(note: schemas.NoteCreate):
    """
    Create a new note for a meeting.
    """
    # Stub: Return a mock note
    return schemas.Note(id=1, content=note.content, meeting_id=note.meeting_id, created_at=datetime.now(timezone.utc))


@app.get("/notes/", response_model=List[schemas.Note])
def list_notes(meeting_id: Optional[int] = Query(None)):
    """
    List all notes, optionally filtered by meeting_id.
    """
    # Stub: Return empty list
    return []


@app.get("/notes/{note_id}", response_model=schemas.Note)
def get_note(note_id: int):
    """
    Get a specific note by ID.
    """
    # Stub: Return a mock note
    return schemas.Note(id=note_id, content="Sample note content", meeting_id=1, created_at=datetime.now(timezone.utc))


# Task endpoints
@app.post("/tasks/", response_model=schemas.Task, status_code=201)
def create_task(task: schemas.TaskCreate):
    """
    Create a new task that is due at a specific meeting.
    """
    # Stub: Return a mock task
    return schemas.Task(
        id=1,
        title=task.title,
        description=task.description,
        status=task.status,
        due_meeting_id=task.due_meeting_id,
        created_at=datetime.now(timezone.utc),
    )


@app.get("/tasks/", response_model=List[schemas.Task])
def list_tasks(meeting_id: Optional[int] = Query(None), status: Optional[schemas.TaskStatus] = Query(None)):
    """
    List all tasks, optionally filtered by meeting_id or status.
    """
    # Stub: Return empty list
    return []


@app.get("/tasks/{task_id}", response_model=schemas.Task)
def get_task(task_id: int):
    """
    Get a specific task by ID.
    """
    # Stub: Return a mock task
    return schemas.Task(
        id=task_id,
        title="Sample task",
        description="Sample task description",
        status="pending",
        due_meeting_id=1,
        created_at=datetime.now(timezone.utc),
    )


@app.put("/tasks/{task_id}", response_model=schemas.Task)
def update_task(task_id: int, task: schemas.TaskUpdate):
    """
    Update a task (e.g., change status, reassign to different meeting).
    """
    # Stub: Return a mock updated task
    return schemas.Task(
        id=task_id,
        title=task.title or "Updated task",
        description=task.description,
        status=task.status or "pending",
        due_meeting_id=task.due_meeting_id or 1,
        created_at=datetime.now(timezone.utc),
    )


@app.delete("/tasks/{task_id}", status_code=204)
def delete_task(task_id: int):
    """
    Delete a task.
    """
    # Stub: Just return success
    return None


@app.get("/")
def root():
    """
    Root endpoint to verify the API is running.
    """
    return {
        "message": "Welcome to the Meeting Notes API",
        "version": "1.0.0",
        "endpoints": {"users": "/users/", "meetings": "/meetings/", "notes": "/notes/", "tasks": "/tasks/"},
    }
