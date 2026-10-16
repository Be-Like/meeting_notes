"""Notes (D5): the first service whose visibility is borrowed from the meeting the row belongs to."""

from sqlalchemy import select
from sqlalchemy.sql import Select

import schemas
from errors import NotFound
from models import Note, attended_meeting_ids
from services.base import BaseService, read_only


class NoteService(BaseService):
    name = "notes"
    invalidates = ("meetings",)

    def scope(self, stmt: Select) -> Select:
        """Visible exactly when self.user_id attends the note's meeting (D4, D5)."""
        return stmt.where(Note.meeting_id.in_(attended_meeting_ids(self.user_id)))

    def create(self, data: schemas.NoteCreate) -> schemas.Note:
        # Called for its NotFound: a hidden and a missing meeting raise before the session is touched (D36).
        self.mesh.meetings.get(meeting_id=data.meeting_id)
        row = Note(content=data.content, meeting_id=data.meeting_id)
        self.session.add(row)
        self.session.flush()
        # The refresh reloads columns through UTCDateTime so the response matches a later read.
        self.session.refresh(row)
        return schemas.Note.model_validate(row)

    @read_only
    def list(self, meeting_id: int | None = None) -> list[schemas.Note]:
        stmt = self.scope(select(Note)).order_by(Note.id)
        if meeting_id is not None:
            stmt = stmt.where(Note.meeting_id == meeting_id)
        return [schemas.Note.model_validate(row) for row in self.session.scalars(stmt)]

    @read_only
    def get(self, note_id: int) -> schemas.Note:
        row = self.session.scalars(self.scope(select(Note)).where(Note.id == note_id)).first()
        if row is None:
            raise NotFound(f"note {note_id} not found")
        return schemas.Note.model_validate(row)
