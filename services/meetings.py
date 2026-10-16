"""Meetings (D4, D6, D7, D9): the first scoped service and the first to call a sibling through the mesh."""

from sqlalchemy import select
from sqlalchemy.orm import selectinload
from sqlalchemy.sql import Select

import schemas
from errors import NotFound
from models import Meeting, User, attended_meeting_ids
from services.base import BaseService, read_only


class MeetingService(BaseService):
    name = "meetings"
    invalidates = ("notes", "tasks")

    def scope(self, stmt: Select) -> Select:
        """Visible exactly when self.user_id attends (D4)."""
        return stmt.where(Meeting.id.in_(attended_meeting_ids(self.user_id)))

    def _owned(self, meeting_id: int) -> Select:
        """The scope plus the creator condition: update and delete start here (D6, D9)."""
        return self.scope(select(Meeting)).where(Meeting.id == meeting_id, Meeting.created_by_id == self.user_id)

    def _attendee_rows(self, ids: list[int]) -> list[User]:
        """Dedupe, add the caller, validate every id through self.mesh.users.get, then load the rows (D7, D14)."""
        wanted = set(ids) | {self.user_id}
        # Every mesh call precedes the first statement on self.session (ARD "The request path").
        for user_id in sorted(wanted):
            self.mesh.users.get(user_id=user_id)
        return list(self.session.scalars(select(User).where(User.id.in_(wanted))).all())

    def _to_schema(self, row: Meeting, details: bool = False) -> schemas.Meeting:
        """Meeting or MeetingWithDetails with attendees, notes, and tasks sorted by id (D33)."""
        meeting = (schemas.MeetingWithDetails if details else schemas.Meeting).model_validate(row)
        meeting.attendees.sort(key=lambda user: user.id)
        if isinstance(meeting, schemas.MeetingWithDetails):
            meeting.notes.sort(key=lambda note: note.id)
            meeting.tasks.sort(key=lambda task: task.id)
        return meeting

    def create(self, data: schemas.MeetingCreate) -> schemas.Meeting:
        rows = self._attendee_rows(data.attendee_ids)
        row = Meeting(
            title=data.title,
            description=data.description,
            scheduled_time=data.scheduled_time,
            created_by_id=self.user_id,
            attendees=rows,
        )
        self.session.add(row)
        self.session.flush()
        # The refresh reloads columns through UTCDateTime so the response matches a later read.
        self.session.refresh(row)
        return self._to_schema(row)

    @read_only
    def list(self) -> list[schemas.Meeting]:
        stmt = (
            self.scope(select(Meeting))
            .options(selectinload(Meeting.attendees))
            .order_by(Meeting.scheduled_time, Meeting.id)
        )
        return [self._to_schema(row) for row in self.session.scalars(stmt)]

    @read_only
    def get(self, meeting_id: int) -> schemas.MeetingWithDetails:
        stmt = (
            self.scope(select(Meeting))
            .where(Meeting.id == meeting_id)
            .options(selectinload(Meeting.attendees), selectinload(Meeting.notes), selectinload(Meeting.tasks))
        )
        row = self.session.scalars(stmt).first()
        if row is None:
            raise NotFound(f"meeting {meeting_id} not found")
        return self._to_schema(row, details=True)

    def update(self, meeting_id: int, data: schemas.MeetingUpdate) -> schemas.Meeting:
        row = self.session.scalars(self._owned(meeting_id)).first()
        if row is None:
            raise NotFound(f"meeting {meeting_id} not found")
        changes = data.model_dump(exclude_unset=True)
        # attendee_ids of null is treated as not sent; an attendee set of nothing has no meaning.
        attendee_ids = changes.pop("attendee_ids", None)
        if attendee_ids is not None:
            row.attendees = self._attendee_rows(attendee_ids)
        for field, value in changes.items():
            setattr(row, field, value)
        self.session.flush()
        self.session.refresh(row)
        return self._to_schema(row)

    def delete(self, meeting_id: int) -> None:
        row = self.session.scalars(self._owned(meeting_id)).first()
        if row is None:
            raise NotFound(f"meeting {meeting_id} not found")
        # The ORM cascade removes notes and tasks and the secondary relationship the meeting_users rows (D30).
        self.session.delete(row)
