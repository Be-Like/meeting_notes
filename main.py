from contextlib import asynccontextmanager
from typing import List, Optional

from fastapi import Depends, FastAPI, Query, Request
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

import auth
import schemas
import services.meetings  # noqa: F401  importing the module registers "meetings" in DEFAULT_REGISTRY (D21)
import services.notes  # noqa: F401  importing the module registers "notes" in DEFAULT_REGISTRY (D21)
import services.tasks  # noqa: F401  importing the module registers "tasks" in DEFAULT_REGISTRY (D21)
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
@app.post("/meetings/", response_model=schemas.Meeting, status_code=201)
def create_meeting(meeting: schemas.MeetingCreate, mesh: ServiceMesh = Depends(auth.get_mesh)):
    """
    Create a new meeting with attendees.
    """
    return mesh.meetings.create(data=meeting)


@app.get("/meetings/", response_model=List[schemas.Meeting])
def list_meetings(mesh: ServiceMesh = Depends(auth.get_mesh)):
    """
    List all meetings.
    """
    return mesh.meetings.list()


@app.get("/meetings/{meeting_id}", response_model=schemas.MeetingWithDetails)
def get_meeting(meeting_id: int, mesh: ServiceMesh = Depends(auth.get_mesh)):
    """
    Get a specific meeting with all details (attendees, notes, tasks).
    """
    return mesh.meetings.get(meeting_id=meeting_id)


@app.put("/meetings/{meeting_id}", response_model=schemas.Meeting)
def update_meeting(meeting_id: int, meeting: schemas.MeetingUpdate, mesh: ServiceMesh = Depends(auth.get_mesh)):
    """
    Update a meeting.
    """
    return mesh.meetings.update(meeting_id=meeting_id, data=meeting)


@app.delete("/meetings/{meeting_id}", status_code=204)
def delete_meeting(meeting_id: int, mesh: ServiceMesh = Depends(auth.get_mesh)):
    """
    Delete a meeting.
    """
    return mesh.meetings.delete(meeting_id=meeting_id)


# Note endpoints
@app.post("/notes/", response_model=schemas.Note, status_code=201)
def create_note(note: schemas.NoteCreate, mesh: ServiceMesh = Depends(auth.get_mesh)):
    """
    Create a new note for a meeting.
    """
    return mesh.notes.create(data=note)


@app.get("/notes/", response_model=List[schemas.Note])
def list_notes(meeting_id: Optional[int] = Query(None), mesh: ServiceMesh = Depends(auth.get_mesh)):
    """
    List all notes, optionally filtered by meeting_id.
    """
    return mesh.notes.list(meeting_id=meeting_id)


@app.get("/notes/{note_id}", response_model=schemas.Note)
def get_note(note_id: int, mesh: ServiceMesh = Depends(auth.get_mesh)):
    """
    Get a specific note by ID.
    """
    return mesh.notes.get(note_id=note_id)


# Task endpoints
@app.post("/tasks/", response_model=schemas.Task, status_code=201)
def create_task(task: schemas.TaskCreate, mesh: ServiceMesh = Depends(auth.get_mesh)):
    """
    Create a new task that is due at a specific meeting.
    """
    return mesh.tasks.create(data=task)


@app.get("/tasks/", response_model=List[schemas.Task])
def list_tasks(
    meeting_id: Optional[int] = Query(None),
    status: Optional[schemas.TaskStatus] = Query(None),
    mesh: ServiceMesh = Depends(auth.get_mesh),
):
    """
    List all tasks, optionally filtered by meeting_id or status.
    """
    return mesh.tasks.list(meeting_id=meeting_id, status=status)


@app.get("/tasks/{task_id}", response_model=schemas.Task)
def get_task(task_id: int, mesh: ServiceMesh = Depends(auth.get_mesh)):
    """
    Get a specific task by ID.
    """
    return mesh.tasks.get(task_id=task_id)


@app.put("/tasks/{task_id}", response_model=schemas.Task)
def update_task(task_id: int, task: schemas.TaskUpdate, mesh: ServiceMesh = Depends(auth.get_mesh)):
    """
    Update a task (e.g., change status, reassign to different meeting).
    """
    return mesh.tasks.update(task_id=task_id, data=task)


@app.delete("/tasks/{task_id}", status_code=204)
def delete_task(task_id: int, mesh: ServiceMesh = Depends(auth.get_mesh)):
    """
    Delete a task.
    """
    return mesh.tasks.delete(task_id=task_id)


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
