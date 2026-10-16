import ast
from datetime import datetime, timezone
from pathlib import Path

import pytest
from sqlalchemy import func, select

import auth
import schemas
from errors import NotFound
from models import Task
from schemas import MeetingCreate, TaskCreate, TaskStatus, TaskUpdate, UserCreate
from services.base import DEFAULT_REGISTRY, is_read_only
from services.tasks import TaskService

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


def task(meeting_id: int, title: str = "Write it up", **fields) -> TaskCreate:
    return TaskCreate(title=title, due_meeting_id=meeting_id, **fields)


def test_create_defaults_status_to_pending_and_get_returns_the_same_task(runtime, seeded):
    ada, grace, _ = seeded
    created_meeting = runtime.build_mesh(ada.id).meetings.create(data=meeting([grace.id]))
    grace_mesh = runtime.build_mesh(grace.id)

    pending = grace_mesh.tasks.create(data=task(created_meeting.id, description="Draft"))
    completed = grace_mesh.tasks.create(data=task(created_meeting.id, status=TaskStatus.completed))

    assert isinstance(pending, schemas.Task)
    assert pending.status == TaskStatus.pending
    assert (pending.title, pending.description, pending.due_meeting_id) == ("Write it up", "Draft", created_meeting.id)
    assert isinstance(pending.id, int)
    assert pending.created_at.tzinfo is not None
    assert completed.status == TaskStatus.completed
    assert runtime.build_mesh(ada.id).tasks.get(task_id=pending.id) == pending
    assert grace_mesh.tasks.get(task_id=completed.id) == completed


def test_create_on_a_hidden_or_missing_meeting_raises_and_persists_nothing(runtime, seeded, db_session):
    ada, grace, linus = seeded
    created_meeting = runtime.build_mesh(ada.id).meetings.create(data=meeting([grace.id]))

    with pytest.raises(NotFound) as hidden:
        runtime.build_mesh(linus.id).tasks.create(data=task(created_meeting.id))
    with pytest.raises(NotFound) as missing:
        runtime.build_mesh(ada.id).tasks.create(data=task(999))

    assert hidden.value.message == f"meeting {created_meeting.id} not found"
    assert missing.value.message == "meeting 999 not found"
    assert db_session.scalar(select(func.count()).select_from(Task)) == 0


def test_list_is_scoped_ordered_by_id_and_narrowed_by_meeting_and_status(runtime, seeded):
    ada, grace, linus = seeded
    ada_mesh = runtime.build_mesh(ada.id)
    grace_mesh = runtime.build_mesh(grace.id)
    linus_mesh = runtime.build_mesh(linus.id)
    shared = ada_mesh.meetings.create(data=meeting([grace.id], title="Shared"))
    private = ada_mesh.meetings.create(data=meeting([], title="Private"))
    first = ada_mesh.tasks.create(data=task(shared.id, "One"))
    second = ada_mesh.tasks.create(data=task(private.id, "Two"))
    third = grace_mesh.tasks.create(data=task(shared.id, "Three", status=TaskStatus.completed))
    fourth = ada_mesh.tasks.create(data=task(private.id, "Four", status=TaskStatus.completed))

    assert ada_mesh.tasks.list() == [first, second, third, fourth]
    assert grace_mesh.tasks.list() == [first, third]
    assert linus_mesh.tasks.list() == []
    assert ada_mesh.tasks.list(meeting_id=shared.id) == [first, third]
    assert ada_mesh.tasks.list(status=TaskStatus.pending) == [first, second]
    assert ada_mesh.tasks.list(status="pending") == [first, second]
    assert ada_mesh.tasks.list(meeting_id=shared.id, status=TaskStatus.completed) == [third]
    assert linus_mesh.tasks.list(meeting_id=shared.id) == []
    assert ada_mesh.tasks.list(meeting_id=999) == []


def test_update_applies_only_sent_fields_and_null_clears_only_the_description(runtime, seeded):
    ada, _, _ = seeded
    ada_mesh = runtime.build_mesh(ada.id)
    created_meeting = ada_mesh.meetings.create(data=meeting([]))
    created = ada_mesh.tasks.create(data=task(created_meeting.id, description="Draft"))

    completed = ada_mesh.tasks.update(task_id=created.id, data=TaskUpdate(status=TaskStatus.completed))
    assert completed == created.model_copy(update={"status": TaskStatus.completed})
    assert ada_mesh.tasks.get(task_id=created.id) == completed

    unchanged = ada_mesh.tasks.update(task_id=created.id, data=TaskUpdate())
    assert unchanged == completed
    assert ada_mesh.tasks.get(task_id=created.id) == unchanged

    cleared = ada_mesh.tasks.update(task_id=created.id, data=TaskUpdate(description=None))
    assert cleared == completed.model_copy(update={"description": None})
    assert ada_mesh.tasks.get(task_id=created.id) == cleared

    titled = ada_mesh.tasks.update(task_id=created.id, data=TaskUpdate(title=None))
    assert titled == cleared
    assert titled.title == "Write it up"
    assert ada_mesh.tasks.get(task_id=created.id) == titled


def test_reassignment_moves_the_task_between_filters_and_detail_views(runtime, seeded):
    ada, _, _ = seeded
    ada_mesh = runtime.build_mesh(ada.id)
    old = ada_mesh.meetings.create(data=meeting([], title="Old"))
    new = ada_mesh.meetings.create(data=meeting([], title="New"))
    created = ada_mesh.tasks.create(data=task(old.id))

    moved = ada_mesh.tasks.update(task_id=created.id, data=TaskUpdate(due_meeting_id=new.id))

    assert moved.due_meeting_id == new.id
    assert ada_mesh.tasks.list(meeting_id=old.id) == []
    assert ada_mesh.tasks.list(meeting_id=new.id) == [moved]
    assert ada_mesh.meetings.get(meeting_id=old.id).tasks == []
    assert [t.id for t in ada_mesh.meetings.get(meeting_id=new.id).tasks] == [created.id]


def test_reassignment_to_a_hidden_or_missing_meeting_raises_and_changes_nothing(runtime, seeded):
    ada, grace, _ = seeded
    ada_mesh = runtime.build_mesh(ada.id)
    created_meeting = ada_mesh.meetings.create(data=meeting([]))
    hidden_meeting = runtime.build_mesh(grace.id).meetings.create(data=meeting([], title="Grace only"))
    created = ada_mesh.tasks.create(data=task(created_meeting.id, description="Draft"))

    with pytest.raises(NotFound) as hidden:
        ada_mesh.tasks.update(task_id=created.id, data=TaskUpdate(title="Changed", due_meeting_id=hidden_meeting.id))
    with pytest.raises(NotFound) as missing:
        ada_mesh.tasks.update(task_id=created.id, data=TaskUpdate(title="Changed", due_meeting_id=999))

    assert hidden.value.message == f"meeting {hidden_meeting.id} not found"
    assert missing.value.message == "meeting 999 not found"
    assert ada_mesh.tasks.get(task_id=created.id) == created


def test_task_reassigned_away_from_a_shared_meeting_is_hidden_from_the_old_attendee(runtime, seeded):
    ada, grace, _ = seeded
    ada_mesh = runtime.build_mesh(ada.id)
    shared = ada_mesh.meetings.create(data=meeting([grace.id], title="Shared"))
    private = ada_mesh.meetings.create(data=meeting([], title="Private"))
    created = ada_mesh.tasks.create(data=task(shared.id))
    grace_mesh = runtime.build_mesh(grace.id)
    assert grace_mesh.tasks.get(task_id=created.id) == created

    ada_mesh.tasks.update(task_id=created.id, data=TaskUpdate(due_meeting_id=private.id))

    with pytest.raises(NotFound) as hidden:
        grace_mesh.tasks.get(task_id=created.id)
    assert hidden.value.message == f"task {created.id} not found"


def test_non_attendee_and_missing_id_get_one_message_and_any_attendee_may_delete(runtime, seeded):
    ada, grace, linus = seeded
    ada_mesh = runtime.build_mesh(ada.id)
    created_meeting = ada_mesh.meetings.create(data=meeting([grace.id]))
    created = ada_mesh.tasks.create(data=task(created_meeting.id))
    linus_mesh = runtime.build_mesh(linus.id)

    attempts = [
        (created.id, lambda: linus_mesh.tasks.update(task_id=created.id, data=TaskUpdate(title="Mine"))),
        (created.id, lambda: linus_mesh.tasks.delete(task_id=created.id)),
        (created.id, lambda: linus_mesh.tasks.get(task_id=created.id)),
        (999, lambda: ada_mesh.tasks.update(task_id=999, data=TaskUpdate(title="Mine"))),
        (999, lambda: ada_mesh.tasks.delete(task_id=999)),
        (999, lambda: ada_mesh.tasks.get(task_id=999)),
    ]
    for task_id, attempt in attempts:
        with pytest.raises(NotFound) as raised:
            attempt()
        assert raised.value.message == f"task {task_id} not found"
    assert ada_mesh.tasks.get(task_id=created.id) == created

    assert runtime.build_mesh(grace.id).tasks.delete(task_id=created.id) is None

    with pytest.raises(NotFound) as gone:
        ada_mesh.tasks.get(task_id=created.id)
    assert gone.value.message == f"task {created.id} not found"
    assert ada_mesh.tasks.list() == []


def test_task_service_is_registered_with_marked_reads_and_invalidates():
    assert DEFAULT_REGISTRY.get("tasks") is TaskService
    assert is_read_only(TaskService.list)
    assert is_read_only(TaskService.get)
    assert not is_read_only(TaskService.create)
    assert not is_read_only(TaskService.update)
    assert not is_read_only(TaskService.delete)
    assert TaskService.invalidates == ("meetings",)


def test_create_and_reassignment_send_one_meetings_get_and_a_plain_update_sends_none(runtime, seeded):
    ada, _, _ = seeded
    ada_mesh = runtime.build_mesh(ada.id)
    old = ada_mesh.meetings.create(data=meeting([], title="Old"))
    new = ada_mesh.meetings.create(data=meeting([], title="New"))

    def recorded(call) -> list[tuple[int, str, str]]:
        sent = len(runtime.transport.envelopes)
        call()
        return [(e.user_id, e.service, e.method) for e in runtime.transport.envelopes[sent:]]

    created = ada_mesh.tasks.create(data=task(old.id))
    assert recorded(lambda: ada_mesh.tasks.create(data=task(old.id))) == [
        (ada.id, "tasks", "create"),
        (ada.id, "meetings", "get"),
    ]
    assert recorded(lambda: ada_mesh.tasks.update(task_id=created.id, data=TaskUpdate(title="Renamed"))) == [
        (ada.id, "tasks", "update"),
    ]
    assert recorded(lambda: ada_mesh.tasks.update(task_id=created.id, data=TaskUpdate(due_meeting_id=new.id))) == [
        (ada.id, "tasks", "update"),
        (ada.id, "meetings", "get"),
    ]


def test_tasks_module_imports_no_sibling_service():
    tree = ast.parse((Path(__file__).parent.parent / "services" / "tasks.py").read_text())
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


def post_task(client, account, meeting_id, title="Write it up", **fields):
    response = client.post(
        "/tasks/", json={"title": title, "due_meeting_id": meeting_id, **fields}, headers=account.headers
    )
    assert response.status_code == 201, response.text
    return response.json()


def test_api_attendee_posts_a_task_with_no_status_and_reads_it_back(client, user_for):
    ada = user_for("ada@example.com", "Ada")
    grace = user_for("grace@example.com", "Grace")
    meeting_id = post_meeting(client, ada, [grace.id])["id"]

    created = post_task(client, grace, meeting_id, description="Draft")

    assert set(created) == {"title", "description", "status", "due_meeting_id", "id", "created_at"}
    assert (created["title"], created["description"], created["status"]) == ("Write it up", "Draft", "pending")
    assert created["due_meeting_id"] == meeting_id
    assert isinstance(created["id"], int)
    assert datetime.fromisoformat(created["created_at"])
    fetched = client.get(f"/tasks/{created['id']}", headers=ada.headers)
    assert fetched.status_code == 200
    assert fetched.json() == created


def test_api_bad_status_is_422_on_create_update_and_filter_and_changes_nothing(client, user_for, db_session):
    ada = user_for("ada@example.com", "Ada")
    meeting_id = post_meeting(client, ada, [])["id"]
    created = post_task(client, ada, meeting_id)
    url = f"/tasks/{created['id']}"

    posted = client.post(
        "/tasks/", json={"title": "Bad", "due_meeting_id": meeting_id, "status": "done"}, headers=ada.headers
    )
    put = client.put(url, json={"status": "done"}, headers=ada.headers)
    filtered = client.get("/tasks/?status=done", headers=ada.headers)

    assert posted.status_code == 422
    assert put.status_code == 422
    assert filtered.status_code == 422
    assert db_session.scalar(select(func.count()).select_from(Task)) == 1
    assert client.get(url, headers=ada.headers).json() == created


def test_api_each_users_task_list_is_scoped_ordered_and_narrowed_by_both_filters(client, user_for):
    ada = user_for("ada@example.com", "Ada")
    grace = user_for("grace@example.com", "Grace")
    linus = user_for("linus@example.com", "Linus")
    shared = post_meeting(client, ada, [grace.id], title="Shared")["id"]
    private = post_meeting(client, ada, [], title="Private")["id"]
    first = post_task(client, ada, shared, "One")
    second = post_task(client, ada, private, "Two")
    third = post_task(client, grace, shared, "Three", status="completed")
    fourth = post_task(client, ada, private, "Four", status="cancelled")

    def listed(account, query=""):
        response = client.get(f"/tasks/{query}", headers=account.headers)
        assert response.status_code == 200
        return response.json()

    assert listed(ada) == [first, second, third, fourth]
    assert listed(grace) == [first, third]
    assert listed(linus) == []
    assert listed(ada, f"?meeting_id={shared}") == [first, third]
    assert listed(ada, "?status=pending") == [first, second]
    assert listed(ada, f"?meeting_id={shared}&status=completed") == [third]
    assert listed(ada, f"?meeting_id={private}&status=completed") == []
    assert listed(linus, f"?meeting_id={shared}") == []
    assert listed(ada, "?meeting_id=999") == []


def test_api_put_applies_only_sent_fields_and_null_clears_only_the_description(client, user_for):
    ada = user_for("ada@example.com", "Ada")
    created = post_task(client, ada, post_meeting(client, ada, [])["id"], description="Draft")
    url = f"/tasks/{created['id']}"

    def put(body):
        response = client.put(url, json=body, headers=ada.headers)
        assert response.status_code == 200, response.text
        assert client.get(url, headers=ada.headers).json() == response.json()
        return response.json()

    completed = put({"status": "completed"})
    assert completed == {**created, "status": "completed"}
    assert put({}) == completed
    cleared = put({"description": None})
    assert cleared == {**completed, "description": None}
    assert put({"title": None}) == cleared


def test_api_reassignment_moves_the_task_between_filters_and_detail_views(client, user_for):
    ada = user_for("ada@example.com", "Ada")
    old = post_meeting(client, ada, [], title="Old")["id"]
    new = post_meeting(client, ada, [], title="New")["id"]
    created = post_task(client, ada, old)

    def where():
        return (
            client.get(f"/tasks/?meeting_id={old}", headers=ada.headers).json(),
            client.get(f"/tasks/?meeting_id={new}", headers=ada.headers).json(),
            client.get(f"/meetings/{old}", headers=ada.headers).json()["tasks"],
            client.get(f"/meetings/{new}", headers=ada.headers).json()["tasks"],
        )

    assert where() == ([created], [], [created], [])

    moved = client.put(f"/tasks/{created['id']}", json={"due_meeting_id": new}, headers=ada.headers)

    assert moved.status_code == 200
    assert moved.json() == {**created, "due_meeting_id": new}
    assert where() == ([], [moved.json()], [], [moved.json()])


def test_api_reassignment_to_a_hidden_or_missing_meeting_is_404_and_changes_nothing(client, user_for):
    ada = user_for("ada@example.com", "Ada")
    grace = user_for("grace@example.com", "Grace")
    created = post_task(client, ada, post_meeting(client, ada, [])["id"])
    hidden_meeting = post_meeting(client, grace, [], title="Grace only")["id"]
    url = f"/tasks/{created['id']}"

    hidden = client.put(url, json={"title": "Changed", "due_meeting_id": hidden_meeting}, headers=ada.headers)
    missing = client.put(url, json={"title": "Changed", "due_meeting_id": 999}, headers=ada.headers)

    assert (hidden.status_code, hidden.json()) == (404, {"detail": f"meeting {hidden_meeting} not found"})
    assert (missing.status_code, missing.json()) == (404, {"detail": "meeting 999 not found"})
    assert client.get(url, headers=ada.headers).json() == created


def test_api_reassigned_task_is_404_for_an_attendee_of_the_old_meeting_only(client, user_for):
    ada = user_for("ada@example.com", "Ada")
    grace = user_for("grace@example.com", "Grace")
    shared = post_meeting(client, ada, [grace.id], title="Shared")["id"]
    private = post_meeting(client, ada, [], title="Private")["id"]
    created = post_task(client, ada, shared)
    url = f"/tasks/{created['id']}"
    assert client.get(url, headers=grace.headers).json() == created

    assert client.put(url, json={"due_meeting_id": private}, headers=ada.headers).status_code == 200

    hidden = client.get(url, headers=grace.headers)
    assert (hidden.status_code, hidden.json()) == (404, {"detail": f"task {created['id']} not found"})
    assert client.get("/tasks/", headers=grace.headers).json() == []


def test_api_non_attendee_and_missing_id_get_404_naming_the_task(client, user_for):
    ada = user_for("ada@example.com", "Ada")
    linus = user_for("linus@example.com", "Linus")
    created = post_task(client, ada, post_meeting(client, ada, [])["id"])

    for account, task_id in ((linus, created["id"]), (ada, 999)):
        url = f"/tasks/{task_id}"
        for response in (
            client.get(url, headers=account.headers),
            client.put(url, json={"title": "Mine"}, headers=account.headers),
            client.delete(url, headers=account.headers),
        ):
            assert (response.status_code, response.json()) == (404, {"detail": f"task {task_id} not found"})
    assert client.get(f"/tasks/{created['id']}", headers=ada.headers).json() == created


def test_api_attendee_who_created_nothing_deletes_the_task_everywhere(client, user_for):
    ada = user_for("ada@example.com", "Ada")
    grace = user_for("grace@example.com", "Grace")
    meeting_id = post_meeting(client, ada, [grace.id])["id"]
    created = post_task(client, ada, meeting_id)
    url = f"/tasks/{created['id']}"
    assert client.get(f"/meetings/{meeting_id}", headers=ada.headers).json()["tasks"] == [created]

    deleted = client.delete(url, headers=grace.headers)

    assert deleted.status_code == 204
    assert deleted.content == b""
    gone = client.get(url, headers=ada.headers)
    assert (gone.status_code, gone.json()) == (404, {"detail": f"task {created['id']} not found"})
    assert client.get("/tasks/", headers=ada.headers).json() == []
    assert client.get(f"/meetings/{meeting_id}", headers=ada.headers).json()["tasks"] == []


def test_api_repeat_task_lists_by_one_user_are_cache_hits_and_another_user_is_not(client, runtime, user_for):
    ada = user_for("ada@example.com", "Ada")
    grace = user_for("grace@example.com", "Grace")
    meeting_id = post_meeting(client, ada, [grace.id])["id"]
    post_task(client, grace, meeting_id)
    sent = len(runtime.transport.envelopes)

    filtered = f"/tasks/?meeting_id={meeting_id}&status=pending"
    for path, expected in (("/tasks/", sent + 1), ("/tasks/", sent + 1), (filtered, sent + 2), (filtered, sent + 2)):
        assert client.get(path, headers=ada.headers).status_code == 200
        assert len(runtime.transport.envelopes) == expected, path

    assert client.get("/tasks/", headers=grace.headers).status_code == 200
    assert len(runtime.transport.envelopes) == sent + 3


def test_api_task_updated_by_one_attendee_shows_in_the_others_next_read(client, runtime, user_for):
    ada = user_for("ada@example.com", "Ada")
    grace = user_for("grace@example.com", "Grace")
    created = post_task(client, ada, post_meeting(client, ada, [grace.id])["id"])
    url = f"/tasks/{created['id']}"
    assert client.get("/tasks/", headers=ada.headers).json() == [created]
    assert client.get(url, headers=ada.headers).json() == created

    assert client.put(url, json={"status": "completed"}, headers=grace.headers).status_code == 200
    sent = len(runtime.transport.envelopes)

    fetched = client.get(url, headers=ada.headers)
    assert fetched.status_code == 200
    assert fetched.json()["status"] == "completed"
    assert len(runtime.transport.envelopes) == sent + 1
