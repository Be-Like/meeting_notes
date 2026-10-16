from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import List, Optional

from fastapi import Depends, FastAPI, Query, Request
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

import auth
import schemas
import services.users  # noqa: F401  importing the module registers "users" in DEFAULT_REGISTRY (D21)
from database import SessionLocal, create_tables, get_db
from errors import Conflict, Invalid, MeshError, NotFound, NoUser, UnknownService
from mesh import MeshRuntime, ServiceMesh, build_runtime


@asynccontextmanager
async def lifespan(app: FastAPI):
    create_tables()
    app.state.mesh_runtime = build_runtime(session_factory=SessionLocal)
    yield


app = FastAPI(
    title="Meeting Notes API",
    description="API for managing meetings, notes, and tasks",
    version="1.0.0",
    lifespan=lifespan,
)


def _mesh_error_handler(status_code: int, headers: dict[str, str] | None = None):
    def handler(request: Request, exc: MeshError) -> JSONResponse:
        return JSONResponse({"detail": exc.message}, status_code=status_code, headers=headers)

    return handler


for _cls, _status in ((NotFound, 404), (Conflict, 409), (Invalid, 422), (UnknownService, 500)):
    app.add_exception_handler(_cls, _mesh_error_handler(_status))
app.add_exception_handler(NoUser, _mesh_error_handler(401, {"WWW-Authenticate": "Bearer"}))


# User endpoints
@app.post("/users/", response_model=schemas.User, status_code=201)
def create_user(
    user: schemas.UserCreate, db: Session = Depends(get_db), runtime: MeshRuntime = Depends(auth.get_runtime)
):
    """
    Create a new user.
    """
    created = auth.register(db, user)
    runtime.cache.clear("users")
    return created


@app.post("/auth/login", response_model=schemas.Token)
def login(body: schemas.LoginRequest, db: Session = Depends(get_db)):
    """
    Log in with email and password; returns a bearer token and revokes any earlier one.
    """
    return schemas.Token(access_token=auth.login(db, body.email, body.password))


@app.get("/users/", response_model=List[schemas.User])
def list_users(mesh: ServiceMesh = Depends(auth.get_mesh)):
    """
    List all users.
    """
    return mesh.users.list()


@app.get("/users/{user_id}", response_model=schemas.User)
def get_user(user_id: int, mesh: ServiceMesh = Depends(auth.get_mesh)):
    """
    Get a specific user by ID.
    """
    return mesh.users.get(user_id=user_id)


# Meeting endpoints
@app.post(
    "/meetings/", response_model=schemas.Meeting, status_code=201, dependencies=[Depends(auth.get_current_user_id)]
)
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


@app.get("/meetings/", response_model=List[schemas.Meeting], dependencies=[Depends(auth.get_current_user_id)])
def list_meetings():
    """
    List all meetings.
    """
    # Stub: Return empty list
    return []


@app.get(
    "/meetings/{meeting_id}",
    response_model=schemas.MeetingWithDetails,
    dependencies=[Depends(auth.get_current_user_id)],
)
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


@app.put("/meetings/{meeting_id}", response_model=schemas.Meeting, dependencies=[Depends(auth.get_current_user_id)])
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


@app.delete("/meetings/{meeting_id}", status_code=204, dependencies=[Depends(auth.get_current_user_id)])
def delete_meeting(meeting_id: int):
    """
    Delete a meeting.
    """
    # Stub: Just return success
    return None


# Note endpoints
@app.post("/notes/", response_model=schemas.Note, status_code=201, dependencies=[Depends(auth.get_current_user_id)])
def create_note(note: schemas.NoteCreate):
    """
    Create a new note for a meeting.
    """
    # Stub: Return a mock note
    return schemas.Note(id=1, content=note.content, meeting_id=note.meeting_id, created_at=datetime.now(timezone.utc))


@app.get("/notes/", response_model=List[schemas.Note], dependencies=[Depends(auth.get_current_user_id)])
def list_notes(meeting_id: Optional[int] = Query(None)):
    """
    List all notes, optionally filtered by meeting_id.
    """
    # Stub: Return empty list
    return []


@app.get("/notes/{note_id}", response_model=schemas.Note, dependencies=[Depends(auth.get_current_user_id)])
def get_note(note_id: int):
    """
    Get a specific note by ID.
    """
    # Stub: Return a mock note
    return schemas.Note(id=note_id, content="Sample note content", meeting_id=1, created_at=datetime.now(timezone.utc))


# Task endpoints
@app.post("/tasks/", response_model=schemas.Task, status_code=201, dependencies=[Depends(auth.get_current_user_id)])
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


@app.get("/tasks/", response_model=List[schemas.Task], dependencies=[Depends(auth.get_current_user_id)])
def list_tasks(meeting_id: Optional[int] = Query(None), status: Optional[schemas.TaskStatus] = Query(None)):
    """
    List all tasks, optionally filtered by meeting_id or status.
    """
    # Stub: Return empty list
    return []


@app.get("/tasks/{task_id}", response_model=schemas.Task, dependencies=[Depends(auth.get_current_user_id)])
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


@app.put("/tasks/{task_id}", response_model=schemas.Task, dependencies=[Depends(auth.get_current_user_id)])
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


@app.delete("/tasks/{task_id}", status_code=204, dependencies=[Depends(auth.get_current_user_id)])
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
