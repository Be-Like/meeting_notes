import ast
from datetime import datetime, timezone
from pathlib import Path

import pytest
from sqlalchemy import func, select

import auth
import schemas
from errors import NotFound
from models import Meeting, Note, Task, meeting_users
from schemas import MeetingCreate, MeetingUpdate, UserCreate
from services.base import DEFAULT_REGISTRY, is_read_only
from services.meetings import MeetingService

JAN_15 = datetime(2026, 1, 15, 10, 0, tzinfo=timezone.utc)


@pytest.fixture
def seeded(db_session) -> list[schemas.User]:
    """Ada, Grace, and Linus registered through auth on db_engine, the engine the runtime's sessions use."""
    return [
        auth.register(db_session, UserCreate(name=name, email=f"{name.lower()}@example.com", password="correct horse"))
        for name in ("Ada", "Grace", "Linus")
    ]


def meeting(attendee_ids: list[int], title: str = "Planning", scheduled_time: datetime = JAN_15) -> MeetingCreate:
    return MeetingCreate(title=title, scheduled_time=scheduled_time, attendee_ids=attendee_ids)


def test_create_sets_creator_adds_them_and_dedupes_and_sorts_attendees(runtime, seeded):
    ada, grace, linus = seeded
    mesh = runtime.build_mesh(grace.id)

    created = mesh.meetings.create(data=meeting([linus.id, ada.id, linus.id]))

    assert isinstance(created, schemas.Meeting)
    assert created.created_by_id == grace.id
    assert created.title == "Planning"
    assert created.scheduled_time == JAN_15
    assert created.attendees == [ada, grace, linus]


def test_list_shows_attended_meetings_in_time_then_id_order(runtime, seeded):
    ada, grace, linus = seeded
    mesh = runtime.build_mesh(ada.id)
    later = mesh.meetings.create(data=meeting([grace.id], title="Later", scheduled_time=datetime(2026, 2, 1)))
    first = mesh.meetings.create(data=meeting([grace.id], title="First"))
    second = mesh.meetings.create(data=meeting([grace.id], title="Second"))

    assert [m.id for m in runtime.build_mesh(grace.id).meetings.list()] == [first.id, second.id, later.id]
    assert runtime.build_mesh(linus.id).meetings.list() == []


def test_get_by_non_attendee_raises_the_same_not_found_as_a_missing_id(runtime, seeded):
    ada, grace, linus = seeded
    created = runtime.build_mesh(ada.id).meetings.create(data=meeting([grace.id]))
    linus_mesh = runtime.build_mesh(linus.id)

    with pytest.raises(NotFound) as hidden:
        linus_mesh.meetings.get(meeting_id=created.id)
    with pytest.raises(NotFound) as missing:
        linus_mesh.meetings.get(meeting_id=created.id + 100)

    assert hidden.value.message == f"meeting {created.id} not found"
    assert missing.value.message == f"meeting {created.id + 100} not found"
    found = runtime.build_mesh(grace.id).meetings.get(meeting_id=created.id)
    assert isinstance(found, schemas.MeetingWithDetails)
    assert found.attendees == [ada, grace]
    assert (found.notes, found.tasks) == ([], [])


def test_update_and_delete_are_creator_only(runtime, seeded):
    ada, grace, _ = seeded
    created = runtime.build_mesh(ada.id).meetings.create(data=meeting([grace.id]))
    grace_mesh = runtime.build_mesh(grace.id)

    with pytest.raises(NotFound) as updated:
        grace_mesh.meetings.update(meeting_id=created.id, data=MeetingUpdate(title="Hijacked"))
    with pytest.raises(NotFound) as deleted:
        grace_mesh.meetings.delete(meeting_id=created.id)
    assert updated.value.message == deleted.value.message == f"meeting {created.id} not found"
    assert grace_mesh.meetings.get(meeting_id=created.id).title == "Planning"

    ada_mesh = runtime.build_mesh(ada.id)
    renamed = ada_mesh.meetings.update(meeting_id=created.id, data=MeetingUpdate(title="Renamed"))
    assert renamed.title == "Renamed"
    assert renamed.attendees == [ada, grace]
    assert ada_mesh.meetings.delete(meeting_id=created.id) is None
    assert ada_mesh.meetings.list() == []
    with pytest.raises(NotFound):
        ada_mesh.meetings.get(meeting_id=created.id)


def test_create_with_unknown_attendee_raises_and_persists_nothing(runtime, seeded, db_session):
    ada, grace, _ = seeded

    with pytest.raises(NotFound) as raised:
        runtime.build_mesh(ada.id).meetings.create(data=meeting([grace.id, 999]))

    assert "999" in raised.value.message
    assert db_session.scalar(select(func.count()).select_from(Meeting)) == 0
    assert db_session.scalar(select(func.count()).select_from(meeting_users)) == 0


def test_update_with_unknown_attendee_raises_and_leaves_the_row_unchanged(runtime, seeded):
    ada, grace, linus = seeded
    mesh = runtime.build_mesh(ada.id)
    created = mesh.meetings.create(data=meeting([grace.id]))

    with pytest.raises(NotFound) as raised:
        mesh.meetings.update(meeting_id=created.id, data=MeetingUpdate(title="Changed", attendee_ids=[linus.id, 999]))

    assert "999" in raised.value.message
    after = mesh.meetings.get(meeting_id=created.id)
    assert after.title == "Planning"
    assert after.attendees == [ada, grace]


def test_meeting_service_is_registered_with_marked_reads_and_invalidates():
    assert DEFAULT_REGISTRY.get("meetings") is MeetingService
    assert is_read_only(MeetingService.list)
    assert is_read_only(MeetingService.get)
    assert not any(is_read_only(getattr(MeetingService, n)) for n in ("create", "update", "delete"))
    assert MeetingService.invalidates == ("notes", "tasks")


def test_create_sends_its_own_envelope_then_one_users_get_per_attendee(runtime, seeded):
    ada, grace, _ = seeded

    runtime.build_mesh(ada.id).meetings.create(data=meeting([grace.id]))

    sent = [(e.user_id, e.service, e.method, e.arguments.get("user_id")) for e in runtime.transport.envelopes]
    assert sent == [
        (ada.id, "meetings", "create", None),
        (ada.id, "users", "get", ada.id),
        (ada.id, "users", "get", grace.id),
    ]


def test_meetings_module_imports_no_sibling_service():
    tree = ast.parse((Path(__file__).parent.parent / "services" / "meetings.py").read_text())
    modules = [node.module for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)]
    modules += [alias.name for node in ast.walk(tree) if isinstance(node, ast.Import) for alias in node.names]

    assert [m for m in modules if m and m.startswith("services.") and m != "services.base"] == []
    assert "services.base" in modules


def post_meeting(client, account, attendee_ids, title="Planning", scheduled_time="2026-01-15T10:00:00Z"):
    response = client.post(
        "/meetings/",
        json={"title": title, "description": "Q1", "scheduled_time": scheduled_time, "attendee_ids": attendee_ids},
        headers=account.headers,
    )
    assert response.status_code == 201, response.text
    return response.json()


def test_api_create_then_get_returns_the_meeting_with_directory_attendees(client, user_for):
    ada = user_for("ada@example.com", "Ada")
    grace = user_for("grace@example.com", "Grace")

    created = post_meeting(client, ada, [grace.id])

    directory = {user["id"]: user for user in client.get("/users/", headers=ada.headers).json()}
    assert created["title"] == "Planning"
    assert created["description"] == "Q1"
    assert datetime.fromisoformat(created["scheduled_time"]) == JAN_15
    assert isinstance(created["id"], int)
    assert datetime.fromisoformat(created["created_at"])
    assert created["created_by_id"] == ada.id
    assert created["attendees"] == [directory[ada.id], directory[grace.id]]

    detail = client.get(f"/meetings/{created['id']}", headers=ada.headers)
    assert detail.status_code == 200
    assert detail.json() == {**created, "notes": [], "tasks": []}


def test_api_create_with_no_attendees_yields_the_creator_alone(client, user_for):
    ada = user_for("ada@example.com", "Ada")

    created = post_meeting(client, ada, [])

    assert [attendee["id"] for attendee in created["attendees"]] == [ada.id]


def test_api_each_users_list_holds_only_meetings_they_attend(client, user_for):
    ada = user_for("ada@example.com", "Ada")
    grace = user_for("grace@example.com", "Grace")
    linus = user_for("linus@example.com", "Linus")
    adas = post_meeting(client, ada, [linus.id], title="Ada's")
    graces = post_meeting(client, grace, [], title="Grace's")

    def listed(account):
        response = client.get("/meetings/", headers=account.headers)
        assert response.status_code == 200
        return [m["id"] for m in response.json()]

    assert listed(ada) == [adas["id"]]
    assert listed(grace) == [graces["id"]]
    assert listed(linus) == [adas["id"]]


def test_api_list_orders_by_scheduled_time_then_id(client, user_for):
    ada = user_for("ada@example.com", "Ada")
    late = post_meeting(client, ada, [], title="Late", scheduled_time="2026-03-01T09:00:00Z")
    early = post_meeting(client, ada, [], title="Early", scheduled_time="2026-01-01T09:00:00Z")
    middle = post_meeting(client, ada, [], title="Middle", scheduled_time="2026-02-01T09:00:00Z")
    tie = post_meeting(client, ada, [], title="Tie", scheduled_time="2026-02-01T09:00:00Z")

    listed = client.get("/meetings/", headers=ada.headers).json()

    assert [m["id"] for m in listed] == [early["id"], middle["id"], tie["id"], late["id"]]


def test_api_get_by_non_attendee_is_404_naming_the_meeting(client, user_for):
    ada = user_for("ada@example.com", "Ada")
    grace = user_for("grace@example.com", "Grace")
    linus = user_for("linus@example.com", "Linus")
    created = post_meeting(client, ada, [grace.id])

    response = client.get(f"/meetings/{created['id']}", headers=linus.headers)

    assert response.status_code == 404
    assert response.json() == {"detail": f"meeting {created['id']} not found"}


def test_api_get_shows_seeded_notes_and_tasks_ordered_by_id(client, user_for, db_session):
    ada = user_for("ada@example.com", "Ada")
    created = post_meeting(client, ada, [])
    notes = [Note(content=c, meeting_id=created["id"]) for c in ("first", "second")]
    tasks = [Task(title=t, due_meeting_id=created["id"]) for t in ("draft", "review")]
    db_session.add_all(notes + tasks)
    db_session.commit()

    detail = client.get(f"/meetings/{created['id']}", headers=ada.headers).json()

    assert detail["notes"] == [schemas.Note.model_validate(n).model_dump(mode="json") for n in notes]
    assert detail["tasks"] == [schemas.Task.model_validate(t).model_dump(mode="json") for t in tasks]
    assert [n["id"] for n in detail["notes"]] == sorted(n.id for n in notes)
    assert [t["id"] for t in detail["tasks"]] == sorted(t.id for t in tasks)


def test_api_put_changes_only_the_sent_fields_and_empty_put_changes_nothing(client, user_for):
    ada = user_for("ada@example.com", "Ada")
    created = post_meeting(client, ada, [])

    renamed = client.put(f"/meetings/{created['id']}", json={"title": "Renamed"}, headers=ada.headers)
    assert renamed.status_code == 200
    assert renamed.json() == {**created, "title": "Renamed"}

    unchanged = client.put(f"/meetings/{created['id']}", json={}, headers=ada.headers)
    assert unchanged.status_code == 200
    assert unchanged.json() == renamed.json()

    detail = client.get(f"/meetings/{created['id']}", headers=ada.headers).json()
    assert detail == {**renamed.json(), "notes": [], "tasks": []}


def test_api_put_attendees_keeps_the_creator_and_moves_visibility(client, user_for):
    ada = user_for("ada@example.com", "Ada")
    grace = user_for("grace@example.com", "Grace")
    linus = user_for("linus@example.com", "Linus")
    created = post_meeting(client, ada, [grace.id])

    response = client.put(f"/meetings/{created['id']}", json={"attendee_ids": [linus.id]}, headers=ada.headers)

    assert response.status_code == 200
    updated = response.json()
    assert updated["created_by_id"] == ada.id
    assert [a["id"] for a in updated["attendees"]] == [ada.id, linus.id]
    assert client.get(f"/meetings/{created['id']}", headers=linus.headers).status_code == 200
    assert [m["id"] for m in client.get("/meetings/", headers=linus.headers).json()] == [created["id"]]
    hidden = client.get(f"/meetings/{created['id']}", headers=grace.headers)
    assert hidden.status_code == 404
    assert hidden.json() == {"detail": f"meeting {created['id']} not found"}
    assert client.get("/meetings/", headers=grace.headers).json() == []


def test_api_unknown_attendee_on_post_and_put_is_404_naming_the_user_and_persists_nothing(client, user_for):
    ada = user_for("ada@example.com", "Ada")
    grace = user_for("grace@example.com", "Grace")

    posted = client.post(
        "/meetings/",
        json={"title": "Planning", "scheduled_time": "2026-01-15T10:00:00Z", "attendee_ids": [grace.id, 999]},
        headers=ada.headers,
    )
    assert posted.status_code == 404
    assert "user 999" in posted.json()["detail"]
    assert client.get("/meetings/", headers=ada.headers).json() == []

    created = post_meeting(client, ada, [grace.id])
    put = client.put(
        f"/meetings/{created['id']}", json={"title": "Changed", "attendee_ids": [999]}, headers=ada.headers
    )
    assert put.status_code == 404
    assert "user 999" in put.json()["detail"]
    detail = client.get(f"/meetings/{created['id']}", headers=ada.headers).json()
    assert detail == {**created, "notes": [], "tasks": []}


def test_api_attendee_who_is_not_the_creator_can_read_but_not_change(client, user_for):
    ada = user_for("ada@example.com", "Ada")
    grace = user_for("grace@example.com", "Grace")
    created = post_meeting(client, ada, [grace.id])
    url = f"/meetings/{created['id']}"
    not_found = {"detail": f"meeting {created['id']} not found"}

    assert client.get(url, headers=grace.headers).status_code == 200
    assert [m["id"] for m in client.get("/meetings/", headers=grace.headers).json()] == [created["id"]]
    put = client.put(url, json={"title": "Hijacked"}, headers=grace.headers)
    assert (put.status_code, put.json()) == (404, not_found)
    deleted = client.delete(url, headers=grace.headers)
    assert (deleted.status_code, deleted.json()) == (404, not_found)
    assert client.get(url, headers=ada.headers).json()["title"] == "Planning"


def test_api_non_attendee_gets_404_on_put_and_delete(client, user_for):
    ada = user_for("ada@example.com", "Ada")
    linus = user_for("linus@example.com", "Linus")
    created = post_meeting(client, ada, [])
    url = f"/meetings/{created['id']}"
    not_found = {"detail": f"meeting {created['id']} not found"}

    put = client.put(url, json={"title": "Hijacked"}, headers=linus.headers)
    assert (put.status_code, put.json()) == (404, not_found)
    deleted = client.delete(url, headers=linus.headers)
    assert (deleted.status_code, deleted.json()) == (404, not_found)
    assert client.get(url, headers=ada.headers).json()["title"] == "Planning"


def test_api_delete_is_204_then_get_is_404_and_the_list_is_empty(client, user_for):
    ada = user_for("ada@example.com", "Ada")
    created = post_meeting(client, ada, [])
    url = f"/meetings/{created['id']}"

    deleted = client.delete(url, headers=ada.headers)

    assert deleted.status_code == 204
    assert deleted.content == b""
    assert client.get(url, headers=ada.headers).status_code == 404
    assert client.get("/meetings/", headers=ada.headers).json() == []


def test_api_delete_cascades_to_seeded_notes_tasks_and_attendee_rows(client, user_for, db_session):
    ada = user_for("ada@example.com", "Ada")
    grace = user_for("grace@example.com", "Grace")
    created = post_meeting(client, ada, [grace.id])
    db_session.add_all(
        [Note(content="first", meeting_id=created["id"]), Task(title="draft", due_meeting_id=created["id"])]
    )
    db_session.commit()

    assert client.delete(f"/meetings/{created['id']}", headers=ada.headers).status_code == 204

    db_session.expire_all()
    for table in (Meeting, Note, Task, meeting_users):
        assert db_session.scalar(select(func.count()).select_from(table)) == 0, table


def test_api_repeat_reads_by_one_user_are_cache_hits_and_another_user_is_not(client, runtime, user_for):
    ada = user_for("ada@example.com", "Ada")
    grace = user_for("grace@example.com", "Grace")
    url = f"/meetings/{post_meeting(client, ada, [grace.id])['id']}"
    sent = len(runtime.transport.envelopes)

    for path, expected in ((url, sent + 1), (url, sent + 1), ("/meetings/", sent + 2), ("/meetings/", sent + 2)):
        assert client.get(path, headers=ada.headers).status_code == 200
        assert len(runtime.transport.envelopes) == expected, path

    assert client.get(url, headers=grace.headers).status_code == 200
    assert len(runtime.transport.envelopes) == sent + 3


def test_api_update_by_one_attendee_shows_on_the_others_next_read(client, runtime, user_for):
    ada = user_for("ada@example.com", "Ada")
    grace = user_for("grace@example.com", "Grace")
    url = f"/meetings/{post_meeting(client, grace, [ada.id])['id']}"
    assert client.get(url, headers=ada.headers).json()["title"] == "Planning"

    assert client.put(url, json={"title": "Renamed"}, headers=grace.headers).status_code == 200
    sent = len(runtime.transport.envelopes)

    detail = client.get(url, headers=ada.headers)
    assert detail.status_code == 200
    assert detail.json()["title"] == "Renamed"
    assert len(runtime.transport.envelopes) == sent + 1
