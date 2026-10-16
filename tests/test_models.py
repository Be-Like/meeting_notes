from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import sessionmaker

from models import Meeting, Note, Task, User, attended_meeting_ids

SCHEDULED = datetime(2026, 1, 15, 10, 0, tzinfo=timezone.utc)


def add_user(db_session, name: str) -> User:
    user = User(name=name, email=f"{name.lower()}@example.com", password_hash="x")
    db_session.add(user)
    db_session.flush()
    return user


def test_deleting_meeting_removes_notes_and_tasks(db_session):
    creator = add_user(db_session, "Ada")
    meeting = Meeting(title="Planning", scheduled_time=SCHEDULED, created_by_id=creator.id)
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
    creator = add_user(db_session, "Ada")
    aware = datetime(2026, 1, 15, 12, 0, tzinfo=timezone(timedelta(hours=2)))
    naive = datetime(2026, 1, 15, 10, 0)
    db_session.add_all(
        [
            Meeting(title="Aware", scheduled_time=aware, created_by_id=creator.id),
            Meeting(title="Naive", scheduled_time=naive, created_by_id=creator.id),
        ]
    )
    db_session.commit()

    with sessionmaker(bind=db_engine)() as fresh:
        read_aware = fresh.query(Meeting).filter_by(title="Aware").one()
        read_naive = fresh.query(Meeting).filter_by(title="Naive").one()

    for meeting in (read_aware, read_naive):
        assert meeting.scheduled_time.tzinfo == timezone.utc
        assert meeting.created_at.tzinfo == timezone.utc
    assert read_aware.scheduled_time == aware
    assert read_naive.scheduled_time == naive.replace(tzinfo=timezone.utc)


def test_attended_meeting_ids_returns_only_meetings_the_user_attends(db_session):
    user = add_user(db_session, "Ada")
    planning = Meeting(title="Planning", scheduled_time=SCHEDULED, created_by_id=user.id, attendees=[user])
    review = Meeting(title="Review", scheduled_time=SCHEDULED, created_by_id=user.id, attendees=[user])
    retro = Meeting(title="Retro", scheduled_time=SCHEDULED, created_by_id=user.id)
    db_session.add_all([planning, review, retro])
    db_session.commit()

    assert sorted(db_session.scalars(attended_meeting_ids(user.id)).all()) == sorted([planning.id, review.id])


def test_attended_meeting_ids_is_empty_for_a_non_attendee_and_never_leaks_across_users(db_session):
    ada = add_user(db_session, "Ada")
    bob = add_user(db_session, "Bob")
    carol = add_user(db_session, "Carol")
    adas = Meeting(title="Ada's", scheduled_time=SCHEDULED, created_by_id=ada.id, attendees=[ada])
    bobs = Meeting(title="Bob's", scheduled_time=SCHEDULED, created_by_id=bob.id, attendees=[bob])
    db_session.add_all([adas, bobs])
    db_session.commit()

    assert db_session.scalars(attended_meeting_ids(carol.id)).all() == []
    assert db_session.scalars(attended_meeting_ids(ada.id)).all() == [adas.id]
    assert bobs.id not in db_session.scalars(attended_meeting_ids(ada.id)).all()
