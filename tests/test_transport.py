from datetime import datetime, timezone

import pytest
from conftest import RecordingTransport
from sqlalchemy.orm import sessionmaker

import auth
import schemas
from errors import NotFound
from mesh import ServiceMesh, build_runtime

# The P6 stories that need the real services and a database. Every test but the routing one runs once per
# transport through the runtime fixture; the routing test builds its own runtime with meetings on json.

JAN_15 = "2026-01-15T10:00:00Z"
JAN_15_UTC = datetime(2026, 1, 15, 10, tzinfo=timezone.utc)
PASSWORD = "correct horse"


@pytest.fixture
def seeded(db_session) -> list[schemas.User]:
    """Ada, Grace, and Linus registered through auth on db_engine, the engine the runtime's sessions use."""
    return [
        auth.register(db_session, schemas.UserCreate(name=name, email=f"{name.lower()}@example.com", password=PASSWORD))
        for name in ("Ada", "Grace", "Linus")
    ]


def mesh_for(runtime, user: schemas.User) -> ServiceMesh:
    return runtime.build_mesh(user.id)


def bearer(client, user: schemas.User) -> dict[str, str]:
    login = client.post("/auth/login", json={"email": user.email, "password": PASSWORD})
    assert login.status_code == 200, login.text
    return {"Authorization": f"Bearer {login.json()['access_token']}"}


def test_meeting_create_and_get_keep_their_types(runtime, client, seeded):
    ada, grace, _ = seeded
    mesh = mesh_for(runtime, ada)

    created = mesh.meetings.create(
        data=schemas.MeetingCreate(title="Planning", scheduled_time=JAN_15, attendee_ids=[grace.id])
    )

    assert type(created) is schemas.Meeting
    assert {a.id for a in created.attendees} == {ada.id, grace.id}
    assert created.scheduled_time == JAN_15_UTC
    fetched = mesh.meetings.get(meeting_id=created.id)
    assert type(fetched) is schemas.MeetingWithDetails
    over_http = client.get(f"/meetings/{created.id}", headers=bearer(client, ada))
    assert fetched == schemas.MeetingWithDetails.model_validate(over_http.json())


def test_partial_update_keeps_the_untouched_fields(runtime, seeded):
    ada, _, _ = seeded
    mesh = mesh_for(runtime, ada)
    created = mesh.meetings.create(
        data=schemas.MeetingCreate(title="Planning", description="Agenda", scheduled_time=JAN_15)
    )

    updated = mesh.meetings.update(meeting_id=created.id, data=schemas.MeetingUpdate(title="Renamed"))

    assert type(updated) is schemas.Meeting
    assert (updated.title, updated.description, updated.scheduled_time) == ("Renamed", "Agenda", JAN_15_UTC)
    fetched = mesh.meetings.get(meeting_id=created.id)
    assert (fetched.title, fetched.description, fetched.scheduled_time) == ("Renamed", "Agenda", JAN_15_UTC)


def test_task_list_tool_matches_the_route_and_hits_the_cache(runtime, client, seeded):
    ada, _, _ = seeded
    mesh = mesh_for(runtime, ada)
    headers = bearer(client, ada)
    created = mesh.meetings.create(data=schemas.MeetingCreate(title="Planning", scheduled_time=JAN_15))
    pending = mesh.tasks.create(data=schemas.TaskCreate(title="Open", due_meeting_id=created.id))
    done = mesh.tasks.create(data=schemas.TaskCreate(title="Done", due_meeting_id=created.id))
    mesh.tasks.update(task_id=done.id, data=schemas.TaskUpdate(status=schemas.TaskStatus.completed))
    n = len(runtime.transport.envelopes)

    from_tool = mesh.call_tool("tasks.list", {"status": "pending"})
    from_route = client.get("/tasks/?status=pending", headers=headers)

    assert from_tool.isError is False
    assert from_tool.structuredContent == {"result": from_route.json()}
    assert len(runtime.transport.envelopes) == n + 1
    listed = mesh.tasks.list(status=schemas.TaskStatus.pending)
    assert [type(task) for task in listed] == [schemas.Task]
    assert (listed[0].id, listed[0].status) == (pending.id, schemas.TaskStatus.pending)


def test_hidden_meeting_is_not_found_with_the_same_body(runtime, client, seeded):
    ada, _, linus = seeded
    created = mesh_for(runtime, ada).meetings.create(data=schemas.MeetingCreate(title="Private", scheduled_time=JAN_15))

    with pytest.raises(NotFound) as raised:
        mesh_for(runtime, linus).meetings.get(meeting_id=created.id)

    assert raised.value.message == f"meeting {created.id} not found"
    response = client.get(f"/meetings/{created.id}", headers=bearer(client, linus))
    assert response.status_code == 404
    assert response.json() == {"detail": f"meeting {created.id} not found"}


def calls(recorder: RecordingTransport) -> list[tuple[int, str, str]]:
    return [(e.user_id, e.service, e.method) for e in recorder.envelopes]


def test_meetings_on_json_and_the_rest_on_memory(db_engine, db_session):
    runtime = build_runtime({"MESH_TRANSPORT_MEETINGS": "json"}, session_factory=sessionmaker(bind=db_engine))
    json_recorder = runtime.transports["json"] = RecordingTransport(runtime.transports["json"])
    memory_recorder = runtime.transports["memory"] = RecordingTransport(runtime.transports["memory"])
    ada, grace = (
        auth.register(db_session, schemas.UserCreate(name=name, email=f"{name.lower()}@example.com", password=PASSWORD))
        for name in ("Ada", "Grace")
    )
    mesh = runtime.build_mesh(ada.id)

    created = mesh.meetings.create(
        data=schemas.MeetingCreate(title="Planning", scheduled_time=JAN_15, attendee_ids=[grace.id])
    )

    assert calls(json_recorder) == [(ada.id, "meetings", "create")]
    assert calls(memory_recorder) == [(ada.id, "users", "get"), (ada.id, "users", "get")]
    json_recorder.envelopes.clear()
    memory_recorder.envelopes.clear()

    mesh.notes.create(data=schemas.NoteCreate(meeting_id=created.id, content="n"))

    assert calls(memory_recorder) == [(ada.id, "notes", "create")]
    assert calls(json_recorder) == [(ada.id, "meetings", "get")]
