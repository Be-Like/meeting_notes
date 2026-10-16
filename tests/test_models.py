from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import sessionmaker

from models import Meeting, Note, Task


def test_deleting_meeting_removes_notes_and_tasks(db_session):
    meeting = Meeting(title="Planning", scheduled_time=datetime(2026, 1, 15, 10, 0, tzinfo=timezone.utc))
    meeting.notes.append(Note(content="Agenda"))
    meeting.tasks.append(Task(title="Send minutes"))
    db_session.add(meeting)
    db_session.commit()

    db_session.delete(meeting)
    db_session.commit()

    assert db_session.query(Meeting).count() == 0
    assert db_session.query(Note).count() == 0
    assert db_session.query(Task).count() == 0


def test_timestamps_round_trip_as_aware_utc(db_engine, db_session):
    aware = datetime(2026, 1, 15, 12, 0, tzinfo=timezone(timedelta(hours=2)))
    naive = datetime(2026, 1, 15, 10, 0)
    db_session.add_all([Meeting(title="Aware", scheduled_time=aware), Meeting(title="Naive", scheduled_time=naive)])
    db_session.commit()

    with sessionmaker(bind=db_engine)() as fresh:
        read_aware = fresh.query(Meeting).filter_by(title="Aware").one()
        read_naive = fresh.query(Meeting).filter_by(title="Naive").one()

    for meeting in (read_aware, read_naive):
        assert meeting.scheduled_time.tzinfo == timezone.utc
        assert meeting.created_at.tzinfo == timezone.utc
    assert read_aware.scheduled_time == aware
    assert read_naive.scheduled_time == naive.replace(tzinfo=timezone.utc)
