"""Tasks (D5, D29): borrowed visibility, a status enum stored as its value, and reassignment through the mesh."""

from sqlalchemy import select
from sqlalchemy.sql import Select

import schemas
from errors import NotFound
from models import Task, attended_meeting_ids
from services.base import BaseService
from tools import tool


class TaskService(BaseService):
    name = "tasks"
    invalidates = ("meetings",)

    def scope(self, stmt: Select) -> Select:
        """Visible exactly when self.user_id attends the meeting the task is due at (D4, D5)."""
        return stmt.where(Task.due_meeting_id.in_(attended_meeting_ids(self.user_id)))

    def _visible(self, task_id: int) -> Task:
        """The scoped row, or NotFound with one message for hidden and missing (D9). Three methods start here."""
        row = self.session.scalars(self.scope(select(Task)).where(Task.id == task_id)).first()
        if row is None:
            raise NotFound(f"task {task_id} not found")
        return row

    @tool(title="Create task", read_only=False, destructive=False, idempotent=False)
    def create(self, data: schemas.TaskCreate) -> schemas.Task:
        """Add a task due at a meeting the caller attends; status defaults to pending. A meeting the caller does not
        attend, or that does not exist, is "meeting <id> not found" and nothing is created. Calling twice adds a
        second task."""
        # Called for its NotFound: a hidden and a missing meeting raise before the session is touched (D36).
        self.mesh.meetings.get(meeting_id=data.due_meeting_id)
        row = Task(
            title=data.title,
            description=data.description,
            status=schemas.TaskStatus(data.status).value,
            due_meeting_id=data.due_meeting_id,
        )
        self.session.add(row)
        self.session.flush()
        # The refresh reloads columns through UTCDateTime so the response matches a later read.
        self.session.refresh(row)
        return schemas.Task.model_validate(row)

    @tool(title="List tasks", read_only=True, destructive=False, idempotent=True)
    def list(self, meeting_id: int | None = None, status: schemas.TaskStatus | None = None) -> list[schemas.Task]:
        """List the tasks on meetings the caller attends, ordered by id, optionally narrowed to one meeting and one
        status. A meeting the caller does not attend yields an empty list."""
        stmt = self.scope(select(Task)).order_by(Task.id)
        if meeting_id is not None:
            stmt = stmt.where(Task.due_meeting_id == meeting_id)
        if status is not None:
            # FastAPI sends the enum and a JSON transport would send its string; both store as the value (D29).
            stmt = stmt.where(Task.status == schemas.TaskStatus(status).value)
        return [schemas.Task.model_validate(row) for row in self.session.scalars(stmt)]

    @tool(title="Get task", read_only=True, destructive=False, idempotent=True)
    def get(self, task_id: int) -> schemas.Task:
        """Return one task due at a meeting the caller attends. A task on a meeting the caller does not attend, or
        that does not exist, is "task <id> not found"."""
        return schemas.Task.model_validate(self._visible(task_id))

    @tool(title="Update task", read_only=False, destructive=True, idempotent=True)
    def update(self, task_id: int, data: schemas.TaskUpdate) -> schemas.Task:
        """Change the fields sent on a task due at a meeting the caller attends; description sent as null clears it.
        A task the caller cannot see, or that does not exist, is "task <id> not found"; moving it to a meeting the
        caller does not attend, or that does not exist, is "meeting <id> not found" and nothing changes."""
        # The scoped read precedes the mesh call because a read leaves nothing to roll back.
        row = self._visible(task_id)
        changes = data.model_dump(exclude_unset=True)
        # title, status, and due_meeting_id sent as null are treated as not sent; description as null clears
        # (PRD outcome 3).
        for field in ("title", "status", "due_meeting_id"):
            if field in changes and changes[field] is None:
                del changes[field]
        if "due_meeting_id" in changes:
            # Validated through the mesh before the first write, so a 404 leaves every field as it was (D36).
            self.mesh.meetings.get(meeting_id=changes["due_meeting_id"])
        if "status" in changes:
            changes["status"] = schemas.TaskStatus(changes["status"]).value
        for field, value in changes.items():
            setattr(row, field, value)
        self.session.flush()
        self.session.refresh(row)
        return schemas.Task.model_validate(row)

    @tool(title="Delete task", read_only=False, destructive=True, idempotent=True)
    def delete(self, task_id: int) -> None:
        """Delete a task due at a meeting the caller attends. A task the caller cannot see, or that does not exist,
        is "task <id> not found"."""
        self.session.delete(self._visible(task_id))
        return None
