import ast
from datetime import datetime, timezone
from pathlib import Path

import pytest
from sqlalchemy import func, select

import auth
import schemas
from errors import NotFound
from models import Note
from schemas import MeetingCreate, NoteCreate, UserCreate
from services.base import DEFAULT_REGISTRY, is_read_only
from services.notes import NoteService

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


def test_create_by_an_attendee_returns_the_note_that_get_returns(runtime, seeded):
    ada, grace, _ = seeded
    created_meeting = runtime.build_mesh(ada.id).meetings.create(data=meeting([grace.id]))

    created = runtime.build_mesh(grace.id).notes.create(
        data=NoteCreate(content="Ship it", meeting_id=created_meeting.id)
    )

    assert isinstance(created, schemas.Note)
    assert (created.content, created.meeting_id) == ("Ship it", created_meeting.id)
    assert isinstance(created.id, int)
    assert created.created_at.tzinfo is not None
    assert runtime.build_mesh(ada.id).notes.get(note_id=created.id) == created


def test_create_on_a_hidden_or_missing_meeting_raises_and_persists_nothing(runtime, seeded, db_session):
    ada, grace, linus = seeded
    created_meeting = runtime.build_mesh(ada.id).meetings.create(data=meeting([grace.id]))
    linus_mesh = runtime.build_mesh(linus.id)

    with pytest.raises(NotFound) as hidden:
        linus_mesh.notes.create(data=NoteCreate(content="Sneaky", meeting_id=created_meeting.id))
    with pytest.raises(NotFound) as missing:
        runtime.build_mesh(ada.id).notes.create(data=NoteCreate(content="Lost", meeting_id=999))

    assert hidden.value.message == f"meeting {created_meeting.id} not found"
    assert missing.value.message == "meeting 999 not found"
    assert db_session.scalar(select(func.count()).select_from(Note)) == 0


def test_list_is_scoped_per_user_ordered_by_id_and_narrowed_by_meeting(runtime, seeded):
    ada, grace, linus = seeded
    ada_mesh = runtime.build_mesh(ada.id)
    shared = ada_mesh.meetings.create(data=meeting([grace.id], title="Shared"))
    private = ada_mesh.meetings.create(data=meeting([], title="Private"))
    first = ada_mesh.notes.create(data=NoteCreate(content="One", meeting_id=shared.id))
    second = ada_mesh.notes.create(data=NoteCreate(content="Two", meeting_id=private.id))
    third = runtime.build_mesh(grace.id).notes.create(data=NoteCreate(content="Three", meeting_id=shared.id))
    linus_mesh = runtime.build_mesh(linus.id)

    assert ada_mesh.notes.list() == [first, second, third]
    assert runtime.build_mesh(grace.id).notes.list() == [first, third]
    assert linus_mesh.notes.list() == []
    assert ada_mesh.notes.list(meeting_id=shared.id) == [first, third]
    assert linus_mesh.notes.list(meeting_id=shared.id) == []
    assert ada_mesh.notes.list(meeting_id=999) == []


def test_get_by_non_attendee_raises_the_same_not_found_as_a_missing_id(runtime, seeded):
    ada, grace, linus = seeded
    ada_mesh = runtime.build_mesh(ada.id)
    created_meeting = ada_mesh.meetings.create(data=meeting([grace.id]))
    note = ada_mesh.notes.create(data=NoteCreate(content="Private", meeting_id=created_meeting.id))
    linus_mesh = runtime.build_mesh(linus.id)

    with pytest.raises(NotFound) as hidden:
        linus_mesh.notes.get(note_id=note.id)
    with pytest.raises(NotFound) as missing:
        linus_mesh.notes.get(note_id=note.id + 100)

    assert hidden.value.message == f"note {note.id} not found"
    assert missing.value.message == f"note {note.id + 100} not found"


def test_note_service_is_registered_with_marked_reads_and_invalidates():
    assert DEFAULT_REGISTRY.get("notes") is NoteService
    assert is_read_only(NoteService.list)
    assert is_read_only(NoteService.get)
    assert not is_read_only(NoteService.create)
    assert NoteService.invalidates == ("meetings",)


def test_each_create_sends_its_own_envelope_then_one_meetings_get(runtime, seeded):
    ada, _, _ = seeded
    ada_mesh = runtime.build_mesh(ada.id)
    created_meeting = ada_mesh.meetings.create(data=meeting([]))
    sent = len(runtime.transport.envelopes)

    ada_mesh.notes.create(data=NoteCreate(content="One", meeting_id=created_meeting.id))
    ada_mesh.notes.create(data=NoteCreate(content="Two", meeting_id=created_meeting.id))

    recorded = [(e.user_id, e.service, e.method) for e in runtime.transport.envelopes[sent:]]
    assert recorded == [
        (ada.id, "notes", "create"),
        (ada.id, "meetings", "get"),
        (ada.id, "notes", "create"),
        (ada.id, "meetings", "get"),
    ]


def test_notes_module_imports_no_sibling_service():
    tree = ast.parse((Path(__file__).parent.parent / "services" / "notes.py").read_text())
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


def post_note(client, account, meeting_id, content="Ship it"):
    response = client.post("/notes/", json={"content": content, "meeting_id": meeting_id}, headers=account.headers)
    assert response.status_code == 201, response.text
    return response.json()


def test_api_attendee_posts_a_note_and_reads_it_back(client, user_for):
    ada = user_for("ada@example.com", "Ada")
    grace = user_for("grace@example.com", "Grace")
    meeting_id = post_meeting(client, ada, [grace.id])["id"]

    created = post_note(client, grace, meeting_id)

    assert set(created) == {"content", "meeting_id", "id", "created_at"}
    assert (created["content"], created["meeting_id"]) == ("Ship it", meeting_id)
    assert isinstance(created["id"], int)
    assert datetime.fromisoformat(created["created_at"])
    fetched = client.get(f"/notes/{created['id']}", headers=ada.headers)
    assert fetched.status_code == 200
    assert fetched.json() == created


def test_api_note_on_a_hidden_or_missing_meeting_is_404_naming_the_meeting(client, user_for):
    ada = user_for("ada@example.com", "Ada")
    grace = user_for("grace@example.com", "Grace")
    linus = user_for("linus@example.com", "Linus")
    meeting_id = post_meeting(client, ada, [grace.id])["id"]

    hidden = client.post("/notes/", json={"content": "Sneaky", "meeting_id": meeting_id}, headers=linus.headers)
    missing = client.post("/notes/", json={"content": "Lost", "meeting_id": 999}, headers=ada.headers)

    assert (hidden.status_code, hidden.json()) == (404, {"detail": f"meeting {meeting_id} not found"})
    assert (missing.status_code, missing.json()) == (404, {"detail": "meeting 999 not found"})
    assert client.get("/notes/", headers=ada.headers).json() == []


def test_api_each_users_list_holds_only_their_meetings_notes_in_id_order(client, user_for):
    ada = user_for("ada@example.com", "Ada")
    grace = user_for("grace@example.com", "Grace")
    linus = user_for("linus@example.com", "Linus")
    shared = post_meeting(client, ada, [grace.id], title="Shared")["id"]
    private = post_meeting(client, ada, [], title="Private")["id"]
    first = post_note(client, ada, shared, "One")
    second = post_note(client, ada, private, "Two")
    third = post_note(client, grace, shared, "Three")

    def listed(account, query=""):
        response = client.get(f"/notes/{query}", headers=account.headers)
        assert response.status_code == 200
        return response.json()

    assert listed(ada) == [first, second, third]
    assert listed(grace) == [first, third]
    assert listed(linus) == []
    assert listed(ada, f"?meeting_id={shared}") == [first, third]
    assert listed(linus, f"?meeting_id={shared}") == []
    assert listed(ada, "?meeting_id=999") == []


def test_api_get_by_non_attendee_or_missing_id_is_404_naming_the_note(client, user_for):
    ada = user_for("ada@example.com", "Ada")
    linus = user_for("linus@example.com", "Linus")
    note_id = post_note(client, ada, post_meeting(client, ada, [])["id"])["id"]

    hidden = client.get(f"/notes/{note_id}", headers=linus.headers)
    missing = client.get("/notes/999", headers=ada.headers)

    assert (hidden.status_code, hidden.json()) == (404, {"detail": f"note {note_id} not found"})
    assert (missing.status_code, missing.json()) == (404, {"detail": "note 999 not found"})


def test_api_repeat_note_lists_by_one_user_are_cache_hits_and_another_user_is_not(client, runtime, user_for):
    ada = user_for("ada@example.com", "Ada")
    grace = user_for("grace@example.com", "Grace")
    meeting_id = post_meeting(client, ada, [grace.id])["id"]
    post_note(client, grace, meeting_id)
    sent = len(runtime.transport.envelopes)

    filtered = f"/notes/?meeting_id={meeting_id}"
    for path, expected in (("/notes/", sent + 1), ("/notes/", sent + 1), (filtered, sent + 2), (filtered, sent + 2)):
        assert client.get(path, headers=ada.headers).status_code == 200
        assert len(runtime.transport.envelopes) == expected, path

    assert client.get("/notes/", headers=grace.headers).status_code == 200
    assert len(runtime.transport.envelopes) == sent + 3


def test_api_note_by_one_attendee_shows_in_the_others_next_meeting_detail(client, runtime, user_for):
    ada = user_for("ada@example.com", "Ada")
    grace = user_for("grace@example.com", "Grace")
    meeting_id = post_meeting(client, ada, [grace.id])["id"]
    url = f"/meetings/{meeting_id}"
    assert client.get(url, headers=ada.headers).json()["notes"] == []

    note = post_note(client, grace, meeting_id)
    sent = len(runtime.transport.envelopes)

    detail = client.get(url, headers=ada.headers)
    assert detail.status_code == 200
    assert detail.json()["notes"] == [note]
    assert len(runtime.transport.envelopes) == sent + 1
