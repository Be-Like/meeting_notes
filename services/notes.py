"""Notes: visibility borrowed from the meeting the note belongs to."""

from sqlalchemy import select
from sqlalchemy.sql import Select

import schemas
from errors import NotFound
from models import Note, attended_meeting_ids
from services.base import BaseService
from tools import tool


class NoteService(BaseService):
    name = "notes"
    invalidates = ("meetings",)

    def scope(self, stmt: Select) -> Select:
        """Visible exactly when self.user_id attends the note's meeting."""
        return stmt.where(Note.meeting_id.in_(attended_meeting_ids(self.user_id)))

    @tool(title="Create note", read_only=False, destructive=False, idempotent=False)
    def create(self, data: schemas.NoteCreate) -> schemas.Note:
        """Add a note to a meeting the caller attends. A meeting the caller does not attend, or that does not exist,
        is "meeting <id> not found" and nothing is created. Calling twice adds a second note."""
        # Called for its NotFound: a hidden or missing meeting raises before the session is touched.
        self.mesh.meetings.get(meeting_id=data.meeting_id)
        row = Note(content=data.content, meeting_id=data.meeting_id)
        self.session.add(row)
        self.session.flush()
        # The refresh reloads columns through UTCDateTime so the response matches a later read.
        self.session.refresh(row)
        return schemas.Note.model_validate(row)

    @tool(title="List notes", read_only=True, destructive=False, idempotent=True)
    def list(self, meeting_id: int | None = None) -> list[schemas.Note]:
        """List the notes on meetings the caller attends, ordered by id, optionally narrowed to one meeting. A
        meeting the caller does not attend yields an empty list."""
        stmt = self.scope(select(Note)).order_by(Note.id)
        if meeting_id is not None:
            stmt = stmt.where(Note.meeting_id == meeting_id)
        return [schemas.Note.model_validate(row) for row in self.session.scalars(stmt)]

    @tool(title="Get note", read_only=True, destructive=False, idempotent=True)
    def get(self, note_id: int) -> schemas.Note:
        """Return one note on a meeting the caller attends. A note on a meeting the caller does not attend, or that
        does not exist, is "note <id> not found"."""
        row = self.session.scalars(self.scope(select(Note)).where(Note.id == note_id)).first()
        if row is None:
            raise NotFound(f"note {note_id} not found")
        return schemas.Note.model_validate(row)
