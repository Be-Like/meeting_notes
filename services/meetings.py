"""Meetings: the first scoped service, and the one that reaches a sibling through the mesh."""

from sqlalchemy import select
from sqlalchemy.orm import selectinload
from sqlalchemy.sql import Select

import schemas
from errors import NotFound
from models import Meeting, User, attended_meeting_ids
from services.base import BaseService
from tools import tool


class MeetingService(BaseService):
    name = "meetings"
    invalidates = ("notes", "tasks")

    def scope(self, stmt: Select) -> Select:
        """Visible exactly when self.user_id attends."""
        return stmt.where(Meeting.id.in_(attended_meeting_ids(self.user_id)))

    def _owned(self, meeting_id: int) -> Select:
        """The scope plus the creator condition; update and delete start here."""
        return self.scope(select(Meeting)).where(Meeting.id == meeting_id, Meeting.created_by_id == self.user_id)

    def _attendee_rows(self, ids: list[int]) -> list[User]:
        wanted = set(ids) | {self.user_id}
        # Every mesh call precedes the first statement on self.session, so a bad id leaves nothing to roll back.
        for user_id in sorted(wanted):
            self.mesh.users.get(user_id=user_id)
        return list(self.session.scalars(select(User).where(User.id.in_(wanted))).all())

    def _to_schema(self, row: Meeting, details: bool = False) -> schemas.Meeting:
        meeting = (schemas.MeetingWithDetails if details else schemas.Meeting).model_validate(row)
        meeting.attendees.sort(key=lambda user: user.id)
        if isinstance(meeting, schemas.MeetingWithDetails):
            meeting.notes.sort(key=lambda note: note.id)
            meeting.tasks.sort(key=lambda task: task.id)
        return meeting

    @tool(title="Create meeting", read_only=False, destructive=False, idempotent=False)
    def create(self, data: schemas.MeetingCreate) -> schemas.Meeting:
        """Create a meeting the caller owns and attends, with the given attendees. Every attendee id must name an
        existing user, or the answer is "user <id> not found" and nothing is created. Calling twice creates two
        meetings."""
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

    @tool(title="List meetings", read_only=True, destructive=False, idempotent=True)
    def list(self) -> list[schemas.Meeting]:
        """List the meetings the caller attends, with their attendees, ordered by scheduled time and then id."""
        stmt = (
            self.scope(select(Meeting))
            .options(selectinload(Meeting.attendees))
            .order_by(Meeting.scheduled_time, Meeting.id)
        )
        return [self._to_schema(row) for row in self.session.scalars(stmt)]

    @tool(title="Get meeting", read_only=True, destructive=False, idempotent=True)
    def get(self, meeting_id: int) -> schemas.MeetingWithDetails:
        """Return one meeting the caller attends, with its attendees, notes, and tasks. A meeting the caller does
        not attend, or that does not exist, is "meeting <id> not found"."""
        stmt = (
            self.scope(select(Meeting))
            .where(Meeting.id == meeting_id)
            .options(selectinload(Meeting.attendees), selectinload(Meeting.notes), selectinload(Meeting.tasks))
        )
        row = self.session.scalars(stmt).first()
        if row is None:
            raise NotFound(f"meeting {meeting_id} not found")
        return self._to_schema(row, details=True)

    @tool(title="Update meeting", read_only=False, destructive=True, idempotent=True)
    def update(self, meeting_id: int, data: schemas.MeetingUpdate) -> schemas.Meeting:
        """Change the fields sent on a meeting the caller created. Sending attendee_ids replaces the attendee list
        and can remove attendees. A meeting the caller did not create, or that does not exist, is "meeting <id>
        not found"."""
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

    @tool(title="Delete meeting", read_only=False, destructive=True, idempotent=True)
    def delete(self, meeting_id: int) -> None:
        """Delete a meeting the caller created, with its notes and tasks. A meeting the caller did not create, or
        that does not exist, is "meeting <id> not found"."""
        row = self.session.scalars(self._owned(meeting_id)).first()
        if row is None:
            raise NotFound(f"meeting {meeting_id} not found")
        # The ORM cascade removes notes and tasks and the secondary relationship the meeting_users rows.
        self.session.delete(row)
