import inspect
import json

import pytest
from pydantic import BaseModel, ValidationError

import auth
import schemas
from errors import Conflict, Invalid, NotFound, UnknownTool
from models import Note
from schemas import UserCreate
from services.base import DEFAULT_REGISTRY, BaseService, ServiceRegistry, is_read_only, read_only
from tools import TOOL_ATTR, ToolDefinition, ToolRegistry, ToolResult, ToolSpec, tool

# tools.py alone: a private registry, two fake services, and a lambda standing in for the mesh hop.

REGISTRY = ServiceRegistry()


class Item(BaseModel):
    id: int
    value: str


class Alpha(BaseService, registry=REGISTRY):
    name = "alpha"

    def scope(self, stmt):
        return stmt

    @tool(title="Find items", read_only=True, destructive=False, idempotent=True)
    def find(self, item_id: int, data: Item, limit: int | None = None) -> list[Item]:
        """Find items.

        Indented second paragraph.
        """
        return [data]

    @tool(title="Put item", read_only=False, destructive=False, idempotent=False)
    def put(self, data: Item) -> Item:
        """Store an item."""
        return data


class Beta(BaseService, registry=REGISTRY):
    name = "beta"

    def scope(self, stmt):
        return stmt

    @tool(title="Drop item", read_only=False, destructive=True, idempotent=True)
    def drop(self, item_id: int) -> None:
        """Drop an item."""

    @read_only
    def peek(self, item_id: int) -> Item:
        return Item(id=item_id, value="peek")


ITEM = {"id": 1, "value": "one"}


class Recorder:
    def __init__(self, value=None, exc: Exception | None = None) -> None:
        self.calls: list[tuple[str, str, dict]] = []
        self.value = value
        self.exc = exc

    def __call__(self, service: str, method: str, arguments: dict):
        self.calls.append((service, method, arguments))
        if self.exc is not None:
            raise self.exc
        return self.value


def test_decorator_returns_the_function_and_records_a_spec():
    def fn(self, item_id: int) -> None:
        """Doc."""

    assert tool(title="T", read_only=False, destructive=False, idempotent=False)(fn) is fn
    assert is_read_only(Alpha.find)
    assert not is_read_only(Alpha.put)
    spec = getattr(Alpha.find, TOOL_ATTR)
    assert isinstance(spec, ToolSpec)
    assert spec.method == "find"
    assert spec.title == "Find items"
    assert spec.description == "Find items.\n\nIndented second paragraph."
    assert (spec.read_only, spec.destructive, spec.idempotent) == (True, False, True)
    assert issubclass(spec.input_model, BaseModel)
    assert issubclass(spec.output_model, BaseModel)


def test_decorator_refuses_bad_tools_at_class_definition():
    with pytest.raises(TypeError):

        class NoDoc:
            @tool(title="T", read_only=False, destructive=False, idempotent=False)
            def go(self) -> None:
                pass

    with pytest.raises(TypeError):

        class BlankDoc:
            @tool(title="T", read_only=False, destructive=False, idempotent=False)
            def go(self) -> None:
                """ """

    with pytest.raises(TypeError):

        class EmptyTitle:
            @tool(title="", read_only=False, destructive=False, idempotent=False)
            def go(self) -> None:
                """Doc."""

    with pytest.raises(TypeError):

        class Underscore:
            @tool(title="T", read_only=False, destructive=False, idempotent=False)
            def _hidden(self) -> None:
                """Doc."""

    with pytest.raises(TypeError):

        class ReadOnlyDestructive:
            @tool(title="T", read_only=True, destructive=True, idempotent=True)
            def go(self) -> None:
                """Doc."""

    with pytest.raises(TypeError):

        class ReadOnlyNotIdempotent:
            @tool(title="T", read_only=True, destructive=False, idempotent=False)
            def go(self) -> None:
                """Doc."""

    with pytest.raises(TypeError):
        tool(title="T", read_only=False, destructive=False)  # type: ignore[call-arg]


def test_input_schema_forbids_extras_and_embeds_models():
    model = getattr(Alpha.find, TOOL_ATTR).input_model
    schema = model.model_json_schema()
    assert schema["additionalProperties"] is False
    assert schema["required"] == ["item_id", "data"]
    assert "self" not in schema["properties"]
    assert schema["properties"]["limit"]["anyOf"] == [{"type": "integer"}, {"type": "null"}]
    assert schema["properties"]["limit"]["default"] is None
    assert schema["properties"]["data"] == {"$ref": "#/$defs/Item"}
    assert "Item" in schema["$defs"]
    with pytest.raises(ValidationError):
        model.model_validate({"item_id": 1, "data": ITEM, "zzz": 1})


def test_output_schema_types_result_from_the_return_hint():
    find = getattr(Alpha.find, TOOL_ATTR).output_model.model_json_schema()
    put = getattr(Alpha.put, TOOL_ATTR).output_model.model_json_schema()
    drop = getattr(Beta.drop, TOOL_ATTR).output_model.model_json_schema()
    for schema in (find, put, drop):
        assert schema["required"] == ["result"]
    assert put["properties"]["result"] == {"$ref": "#/$defs/Item"}
    assert find["properties"]["result"]["type"] == "array"
    assert find["properties"]["result"]["items"] == {"$ref": "#/$defs/Item"}
    assert drop["properties"]["result"]["type"] == "null"


def test_registry_lists_decorated_methods_with_exactly_the_mcp_fields():
    registry = ToolRegistry(REGISTRY)
    assert [d.name for d in registry.definitions] == ["alpha.find", "alpha.put", "beta.drop"]
    for definition in registry.definitions:
        dumped = definition.model_dump()
        assert set(dumped) == {"name", "title", "description", "inputSchema", "outputSchema", "annotations"}
        assert set(dumped["annotations"]) == {"readOnlyHint", "destructiveHint", "idempotentHint", "openWorldHint"}
        assert dumped["annotations"]["openWorldHint"] is False
    find = registry.definitions[0]
    assert find.title == "Find items"
    assert find.annotations.readOnlyHint is True
    assert find.inputSchema == getattr(Alpha.find, TOOL_ATTR).input_model.model_json_schema()
    assert registry.definitions == ToolRegistry(REGISTRY).definitions
    with pytest.raises(ValidationError):
        find.title = "changed"


def test_valid_call_sends_validated_values_and_wraps_the_result():
    send = Recorder(value=[Item(id=1, value="one")])
    result = ToolRegistry(REGISTRY).call("alpha.find", {"item_id": 1, "data": ITEM}, send)
    assert send.calls == [("alpha", "find", {"item_id": 1, "data": Item(id=1, value="one"), "limit": None})]
    assert result.isError is False
    assert result.structuredContent == {"result": [ITEM]}
    assert result.content[0].type == "text"
    assert json.loads(result.content[0].text) == result.structuredContent


def test_invalid_arguments_are_an_error_result_and_nothing_is_sent():
    send = Recorder()
    result = ToolRegistry(REGISTRY).call("beta.drop", {"item_id": "x", "zzz": 1}, send)
    assert result.isError is True
    assert result.structuredContent is None
    lines = result.content[0].text.split("\n")
    assert lines[0].startswith("item_id: ")
    assert lines[1] == "zzz: Extra inputs are not permitted"
    assert send.calls == []


@pytest.mark.parametrize("exc", [NotFound("x 1 not found"), Conflict("x 1 taken"), Invalid("x 1 bad")])
def test_mesh_error_from_send_is_an_error_result(exc):
    result = ToolRegistry(REGISTRY).call("beta.drop", {"item_id": 1}, Recorder(exc=exc))
    assert result.isError is True
    assert result.content[0].text == exc.message
    assert result.structuredContent is None


def test_other_exceptions_from_send_propagate():
    with pytest.raises(RuntimeError):
        ToolRegistry(REGISTRY).call("beta.drop", {"item_id": 1}, Recorder(exc=RuntimeError("boom")))


def test_none_result_is_a_null_result():
    result = ToolRegistry(REGISTRY).call("beta.drop", {"item_id": 1}, Recorder(value=None))
    assert result.isError is False
    assert result.structuredContent == {"result": None}
    assert result.content[0].text == '{"result": null}'


def test_unknown_tool_raises_and_nothing_is_sent():
    registry = ToolRegistry(REGISTRY)
    send = Recorder()
    with pytest.raises(UnknownTool, match="alpha.nope"):
        registry.get("alpha.nope")
    with pytest.raises(UnknownTool, match="alpha.nope"):
        registry.call("alpha.nope", {}, send)
    assert send.calls == []


# Through the runtime: the real registry, users seeded by auth on db_engine, the engine the runtime's sessions use.

# Title, readOnlyHint, destructiveHint, idempotentHint per tool. A method added without @tool, a tool removed, or a
# hint changed fails the tests below.
HINTS = {
    "meetings.create": ("Create meeting", False, False, False),
    "meetings.delete": ("Delete meeting", False, True, True),
    "meetings.get": ("Get meeting", True, False, True),
    "meetings.list": ("List meetings", True, False, True),
    "meetings.update": ("Update meeting", False, True, True),
    "notes.create": ("Create note", False, False, False),
    "notes.get": ("Get note", True, False, True),
    "notes.list": ("List notes", True, False, True),
    "tasks.create": ("Create task", False, False, False),
    "tasks.delete": ("Delete task", False, True, True),
    "tasks.get": ("Get task", True, False, True),
    "tasks.list": ("List tasks", True, False, True),
    "tasks.update": ("Update task", False, True, True),
    "users.get": ("Get user", True, False, True),
    "users.list": ("List users", True, False, True),
}
EXPECTED_TOOLS = [
    "meetings.create",
    "meetings.delete",
    "meetings.get",
    "meetings.list",
    "meetings.update",
    "notes.create",
    "notes.get",
    "notes.list",
    "tasks.create",
    "tasks.delete",
    "tasks.get",
    "tasks.list",
    "tasks.update",
    "users.get",
    "users.list",
]
SERVICES = ("users", "meetings", "notes", "tasks")


@pytest.fixture
def seeded(db_session) -> list[schemas.User]:
    """Ada, Grace, and Linus registered through auth on db_engine, the engine the runtime's sessions use."""
    return [
        auth.register(db_session, UserCreate(name=name, email=f"{name.lower()}@example.com", password="correct horse"))
        for name in ("Ada", "Grace", "Linus")
    ]


def test_runtime_builds_the_tool_view_once_and_every_mesh_carries_it(runtime, seeded):
    ada, _, _ = seeded
    assert isinstance(runtime.tools, ToolRegistry)
    mesh = runtime.build_mesh(ada.id)
    tools = mesh.get_tools()
    assert isinstance(tools, list)
    assert all(isinstance(definition, ToolDefinition) for definition in tools)
    assert tools == mesh.get_tools()
    with pytest.raises(UnknownTool, match="nope.nope"):
        mesh.call_tool("nope.nope", {})


def test_every_public_service_method_is_a_tool(runtime, seeded):
    ada, _, _ = seeded
    assert [definition.name for definition in runtime.build_mesh(ada.id).get_tools()] == EXPECTED_TOOLS
    for service in SERVICES:
        cls = DEFAULT_REGISTRY.get(service)
        public = [n for n in dir(cls) if not n.startswith("_") and n != "scope" and callable(getattr(cls, n))]
        assert public
        for attr in public:
            assert isinstance(getattr(getattr(cls, attr), TOOL_ATTR, None), ToolSpec), f"{service}.{attr}"


def test_tool_list_is_the_same_for_every_call_and_caller(runtime, seeded):
    ada, grace, _ = seeded
    first = runtime.build_mesh(ada.id).get_tools()
    second = runtime.build_mesh(ada.id).get_tools()
    other = runtime.build_mesh(grace.id).get_tools()
    assert first == second == other
    assert [d.name for d in first] == sorted(d.name for d in first)


def test_schemas_embed_the_service_models(runtime, seeded):
    ada, _, _ = seeded
    tools = {d.name: d for d in runtime.build_mesh(ada.id).get_tools()}
    create = tools["meetings.create"].inputSchema
    assert create["properties"]["data"] == {"$ref": "#/$defs/MeetingCreate"}
    assert create["required"] == ["data"]
    assert create["additionalProperties"] is False
    assert "MeetingCreate" in create["$defs"]
    get = tools["meetings.get"].outputSchema
    assert get["properties"]["result"] == {"$ref": "#/$defs/MeetingWithDetails"}
    assert get["required"] == ["result"]
    assert {"User", "Note", "Task", "MeetingWithDetails"} <= set(get["$defs"])


def test_titles_hints_and_descriptions_follow_the_methods(runtime, seeded):
    ada, _, _ = seeded
    for definition in runtime.build_mesh(ada.id).get_tools():
        service, method = definition.name.split(".")
        fn = getattr(DEFAULT_REGISTRY.get(service), method)
        annotations = definition.annotations
        assert annotations.readOnlyHint == is_read_only(fn), definition.name
        assert annotations.openWorldHint is False
        title, *hints = HINTS[definition.name]
        assert definition.title == title
        assert [annotations.readOnlyHint, annotations.destructiveHint, annotations.idempotentHint] == hints
        assert definition.description
        assert definition.description == inspect.getdoc(fn)


def test_optional_filters_are_typed_and_defaulted(runtime, seeded):
    ada, _, _ = seeded
    tools = {d.name: d for d in runtime.build_mesh(ada.id).get_tools()}
    tasks = tools["tasks.list"].inputSchema
    status = tasks["properties"]["status"]
    assert status["anyOf"] == [{"$ref": "#/$defs/TaskStatus"}, {"type": "null"}]
    assert status["default"] is None
    assert all(branch.get("type") != "string" for branch in status["anyOf"])
    assert tasks["$defs"]["TaskStatus"]["enum"] == ["pending", "completed", "cancelled"]
    notes = tools["notes.list"].inputSchema
    assert notes["properties"]["meeting_id"]["anyOf"] == [{"type": "integer"}, {"type": "null"}]
    assert notes["properties"]["meeting_id"]["default"] is None
    assert "required" not in notes


# call_tool through the runtime beside the HTTP client on one database: the same user, rows, scope, and cache.

JAN_15 = "2026-01-15T10:00:00Z"


def meeting(attendee_ids: list[int], title: str = "Planning", scheduled_time: str = JAN_15) -> dict:
    return {"title": title, "scheduled_time": scheduled_time, "attendee_ids": attendee_ids}


def call_tool(runtime, user_id: int, name: str, arguments: dict) -> ToolResult:
    return runtime.build_mesh(user_id).call_tool(name, arguments)


def test_tool_create_is_a_result_the_attendee_reads_over_http(runtime, client, user_for):
    ada = user_for("ada@example.com", "Ada")
    grace = user_for("grace@example.com", "Grace")

    created = call_tool(runtime, ada.id, "meetings.create", {"data": meeting([grace.id])})

    assert created.isError is False
    result = created.structuredContent["result"]
    assert result["title"] == "Planning"
    assert result["created_by_id"] == ada.id
    assert [a["id"] for a in result["attendees"]] == [ada.id, grace.id]
    assert json.loads(created.content[0].text) == created.structuredContent
    fetched = client.get(f"/meetings/{result['id']}", headers=grace.headers)
    assert fetched.status_code == 200
    assert (fetched.json()["id"], fetched.json()["title"]) == (result["id"], "Planning")


def test_bad_arguments_are_an_error_result_and_send_nothing(runtime, client, user_for):
    ada = user_for("ada@example.com", "Ada")
    assert call_tool(runtime, ada.id, "meetings.create", {"data": meeting([])}).isError is False
    n = len(runtime.transport.envelopes)

    wrong_type = call_tool(runtime, ada.id, "meetings.get", {"meeting_id": "x"})
    unknown = call_tool(runtime, ada.id, "meetings.get", {"meeting_id": 1, "bogus": 1})

    assert (wrong_type.isError, unknown.isError) == (True, True)
    assert "meeting_id" in wrong_type.content[0].text
    assert "bogus" in unknown.content[0].text
    assert (wrong_type.structuredContent, unknown.structuredContent) == (None, None)
    assert len(runtime.transport.envelopes) == n


def test_non_attendee_gets_the_http_not_found_words_and_writes_nothing(runtime, client, user_for, db_session):
    ada = user_for("ada@example.com", "Ada")
    grace = user_for("grace@example.com", "Grace")
    linus = user_for("linus@example.com", "Linus")
    meeting_id = call_tool(runtime, ada.id, "meetings.create", {"data": meeting([grace.id])}).structuredContent[
        "result"
    ]["id"]

    read = call_tool(runtime, linus.id, "meetings.get", {"meeting_id": meeting_id})
    note = call_tool(runtime, linus.id, "notes.create", {"data": {"content": "Sneaky", "meeting_id": meeting_id}})

    expected = f"meeting {meeting_id} not found"
    assert read.isError is True
    assert read.content[0].text == expected
    assert client.get(f"/meetings/{meeting_id}", headers=linus.headers).json()["detail"] == expected
    assert note.isError is True
    assert note.content[0].text == expected
    assert db_session.query(Note).count() == 0


def test_delete_is_a_null_result_and_the_meeting_is_gone(runtime, client, user_for):
    ada = user_for("ada@example.com", "Ada")
    meeting_id = call_tool(runtime, ada.id, "meetings.create", {"data": meeting([])}).structuredContent["result"]["id"]

    deleted = call_tool(runtime, ada.id, "meetings.delete", {"meeting_id": meeting_id})

    assert deleted.isError is False
    assert deleted.structuredContent == {"result": None}
    assert deleted.content[0].text == '{"result": null}'
    assert call_tool(runtime, ada.id, "meetings.get", {"meeting_id": meeting_id}).isError is True


def test_unknown_tool_name_raises_and_sends_nothing(runtime, client, user_for):
    ada = user_for("ada@example.com", "Ada")
    n = len(runtime.transport.envelopes)

    with pytest.raises(UnknownTool, match="meetings.nope"):
        call_tool(runtime, ada.id, "meetings.nope", {})

    assert len(runtime.transport.envelopes) == n


def test_meeting_posted_over_http_is_scoped_out_of_another_users_tools(runtime, client, user_for):
    ada = user_for("ada@example.com", "Ada")
    grace = user_for("grace@example.com", "Grace")
    posted = client.post("/meetings/", json=meeting([]), headers=ada.headers)
    assert posted.status_code == 201
    meeting_id = posted.json()["id"]

    assert call_tool(runtime, grace.id, "meetings.list", {}).structuredContent == {"result": []}
    assert call_tool(runtime, grace.id, "meetings.get", {"meeting_id": meeting_id}).isError is True
    listed = call_tool(runtime, ada.id, "meetings.list", {}).structuredContent["result"]
    assert [m["id"] for m in listed] == [meeting_id]


def test_http_and_tool_reads_by_one_user_share_a_cache_entry(runtime, client, user_for):
    ada = user_for("ada@example.com", "Ada")
    first = call_tool(runtime, ada.id, "meetings.create", {"data": meeting([])}).structuredContent["result"]["id"]
    second = call_tool(runtime, ada.id, "meetings.create", {"data": meeting([], title="Retro")}).structuredContent[
        "result"
    ]["id"]

    assert client.get(f"/meetings/{first}", headers=ada.headers).status_code == 200
    n = len(runtime.transport.envelopes)
    read = call_tool(runtime, ada.id, "meetings.get", {"meeting_id": first})
    assert read.isError is False
    assert (read.structuredContent["result"]["id"], read.structuredContent["result"]["title"]) == (first, "Planning")
    assert len(runtime.transport.envelopes) == n

    assert call_tool(runtime, ada.id, "meetings.get", {"meeting_id": second}).isError is False
    n = len(runtime.transport.envelopes)
    fetched = client.get(f"/meetings/{second}", headers=ada.headers)
    assert fetched.status_code == 200
    assert fetched.json()["title"] == "Retro"
    assert len(runtime.transport.envelopes) == n


def test_tool_write_clears_the_cached_http_read(runtime, client, user_for):
    ada = user_for("ada@example.com", "Ada")
    grace = user_for("grace@example.com", "Grace")
    meeting_id = call_tool(runtime, ada.id, "meetings.create", {"data": meeting([grace.id])}).structuredContent[
        "result"
    ]["id"]
    posted = client.post("/tasks/", json={"title": "Write it up", "due_meeting_id": meeting_id}, headers=ada.headers)
    assert posted.status_code == 201
    task_id = posted.json()["id"]
    assert client.get(f"/tasks/{task_id}", headers=ada.headers).json()["status"] == "pending"

    updated = call_tool(runtime, grace.id, "tasks.update", {"task_id": task_id, "data": {"status": "completed"}})

    assert updated.isError is False
    assert updated.structuredContent["result"]["status"] == "completed"
    n = len(runtime.transport.envelopes)
    fetched = client.get(f"/tasks/{task_id}", headers=ada.headers)
    assert fetched.status_code == 200
    assert fetched.json()["status"] == "completed"
    assert len(runtime.transport.envelopes) == n + 1


# GET /tools over HTTP: guarded like every route, the same list the runtime builds, and nothing else under /tools.


@pytest.mark.parametrize("headers", [{}, {"Authorization": "Bearer nope"}], ids=["no-header", "unknown-bearer"])
def test_tools_route_without_valid_bearer_returns_401(client, headers):
    response = client.get("/tools", headers=headers)

    assert response.status_code == 401
    assert response.headers["WWW-Authenticate"] == "Bearer"
    assert response.json() == {"detail": auth.TOKEN_FAILED}


def test_tools_route_returns_the_runtime_tool_list(runtime, client, user_for):
    ada = user_for("ada@example.com", "Ada")

    response = client.get("/tools", headers=ada.headers)

    assert response.status_code == 200
    expected = [t.model_dump() for t in runtime.build_mesh(ada.id).get_tools()]
    assert len(expected) == 15
    assert response.json() == {"tools": expected}


def test_root_lists_the_tools_route(client):
    assert client.get("/").json()["endpoints"]["tools"] == "/tools"


def test_openapi_guards_get_tools_and_exposes_nothing_else_under_it(client):
    paths = client.get("/openapi.json").json()["paths"]

    assert paths["/tools"]["get"]["security"] == [{"HTTPBearer": []}]
    assert [path for path in paths if path.startswith("/tools")] == ["/tools"]
    assert list(paths["/tools"]) == ["get"]
