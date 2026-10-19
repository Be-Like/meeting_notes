from datetime import datetime, timezone

import pytest
from conftest import RecordingTransport
from pydantic import BaseModel, ValidationError

import schemas
from cache import InMemoryCache, cache_key
from errors import ERROR_TYPES, ErrorPayload, NotFound, NoUser, UnknownService, from_payload, to_payload
from mesh import (
    CallEnvelope,
    Dispatcher,
    InMemoryTransport,
    JsonTransport,
    MeshRuntime,
    Reply,
    ServiceMesh,
    TransportRouter,
    build_runtime,
)
from services.base import DEFAULT_REGISTRY, BaseService, ServiceRegistry, is_read_only, read_only
from tools import ToolRegistry

REGISTRY = ServiceRegistry()
CALLS: list[tuple[str, str, dict]] = []


class Item(BaseModel):
    id: int
    value: str


class Alpha(BaseService, registry=REGISTRY):
    name = "alpha"
    invalidates = ("beta",)

    def scope(self, stmt):
        return stmt

    @read_only
    def get(self, item_id: int) -> Item:
        CALLS.append((self.name, "get", {"item_id": item_id}))
        return Item(id=item_id, value=f"alpha {item_id}")

    def put(self, item_id: int, value: str) -> Item:
        CALLS.append((self.name, "put", {"item_id": item_id, "value": value}))
        return Item(id=item_id, value=value)

    def peek(self, item_id: int) -> Item:
        CALLS.append((self.name, "peek", {"item_id": item_id}))
        return Item(id=item_id, value=f"alpha {item_id}")

    def missing(self, item_id: int) -> Item:
        CALLS.append((self.name, "missing", {"item_id": item_id}))
        raise NotFound(f"item {item_id} not found")

    def boom(self) -> None:
        CALLS.append((self.name, "boom", {}))
        raise RuntimeError("boom")

    @read_only
    def nothing(self) -> None:
        CALLS.append((self.name, "nothing", {}))
        return None

    def echo_update(self, data: schemas.MeetingUpdate) -> schemas.MeetingUpdate:
        CALLS.append((self.name, "echo_update", {"data": data}))
        return data

    def echo_when(self, when: datetime) -> datetime:
        CALLS.append((self.name, "echo_when", {"when": when}))
        return when

    def echo_status(self, status: schemas.TaskStatus) -> schemas.TaskStatus:
        CALLS.append((self.name, "echo_status", {"status": status}))
        return status

    def items(self) -> list[Item]:
        CALLS.append((self.name, "items", {}))
        return [Item(id=1, value="a"), Item(id=2, value="b")]

    def put_via_beta(self, item_id: int, value: str) -> Item:
        CALLS.append((self.name, "put_via_beta", {"item_id": item_id, "value": value}))
        self.mesh.beta.put(item_id=item_id, value=value)
        raise NotFound(f"item {item_id} not found")


class Beta(BaseService, registry=REGISTRY):
    name = "beta"

    def scope(self, stmt):
        return stmt

    @read_only
    def get(self, item_id: int) -> Item:
        CALLS.append((self.name, "get", {"item_id": item_id}))
        return Item(id=item_id, value=f"beta {item_id}")

    def put(self, item_id: int, value: str) -> Item:
        CALLS.append((self.name, "put", {"item_id": item_id, "value": value}))
        return Item(id=item_id, value=value)

    @read_only
    def get_via_alpha(self, item_id: int) -> Item:
        CALLS.append((self.name, "get_via_alpha", {"item_id": item_id}))
        return self.mesh.alpha.get(item_id=item_id)


class FakeSession:
    def __init__(self) -> None:
        self.log: list[str] = []

    def commit(self) -> None:
        self.log.append("commit")

    def rollback(self) -> None:
        self.log.append("rollback")

    def close(self) -> None:
        self.log.append("close")


class FakeSessionFactory:
    """Records every session it hands out so a test can read the log of the one a dispatch opened."""

    def __init__(self) -> None:
        self.sessions: list[FakeSession] = []

    def __call__(self) -> FakeSession:
        session = FakeSession()
        self.sessions.append(session)
        return session


class Clock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


class MeshParts:
    """The production wiring from build_runtime over REGISTRY and fake sessions, with the cache swapped for one on
    a controllable clock and the transport wrapped so every send, near or far, is recorded."""

    def __init__(self, transport: str = "memory") -> None:
        self.clock = Clock()
        self.sessions = FakeSessionFactory()
        self.runtime = build_runtime({"MESH_TRANSPORT": transport}, registry=REGISTRY, session_factory=self.sessions)
        self.runtime.cache = self.cache = InMemoryCache(ttl_seconds=60, clock=self.clock)
        self.runtime.transport = self.transport = RecordingTransport(self.runtime.transport)
        self.build_mesh = self.runtime.build_mesh

    def sent(self) -> list[tuple[int, str, str]]:
        return [(e.user_id, e.service, e.method) for e in self.transport.envelopes]


@pytest.fixture(params=["memory", "json"])
def parts(request):
    return MeshParts(request.param)


@pytest.fixture(autouse=True)
def clear_calls():
    CALLS.clear()
    yield
    CALLS.clear()


@pytest.fixture
def sessions():
    return FakeSessionFactory()


@pytest.fixture
def meshes():
    """A stub mesh factory: returns a distinct marker per user id and records which ids it was asked for."""

    class StubMeshFactory:
        def __init__(self) -> None:
            self.built: list[tuple[int, object]] = []

        def __call__(self, user_id: int) -> object:
            mesh = object()
            self.built.append((user_id, mesh))
            return mesh

    return StubMeshFactory()


@pytest.fixture
def dispatcher(sessions, meshes):
    return Dispatcher(REGISTRY, sessions, meshes)


def envelope(service: str, method: str, user_id: int = 7, **arguments) -> CallEnvelope:
    return CallEnvelope(user_id=user_id, service=service, method=method, arguments=arguments)


def test_duplicate_service_name_raises_type_error():
    with pytest.raises(TypeError, match="alpha"):

        class AlphaAgain(BaseService, registry=REGISTRY):
            name = "alpha"

            def scope(self, stmt):
                return stmt

    assert REGISTRY.get("alpha") is Alpha


@pytest.mark.parametrize("bad_name", ["1bad", "_private", "has-dash", ""])
def test_bad_service_name_raises_type_error(bad_name):
    registry = ServiceRegistry()
    with pytest.raises(TypeError):

        class Bad(BaseService, registry=registry):
            name = bad_name

            def scope(self, stmt):
                return stmt

    assert registry.names() == []


def test_service_without_scope_cannot_be_instantiated():
    registry = ServiceRegistry()

    class NoScope(BaseService, registry=registry):
        name = "noscope"

    assert "noscope" in registry
    with pytest.raises(TypeError, match="scope"):
        NoScope(mesh=object(), user_id=7, session=object())


def test_service_stores_mesh_user_and_session():
    mesh, session = object(), object()
    service = Alpha(mesh=mesh, user_id=7, session=session)
    assert service.mesh is mesh
    assert service.user_id == 7
    assert service.session is session


def test_unnamed_subclass_is_not_registered():
    registry = ServiceRegistry()

    class Intermediate(BaseService, registry=registry):
        pass

    assert registry.names() == []


def test_registry_get_unknown_name_raises_unknown_service():
    with pytest.raises(UnknownService, match="nope"):
        REGISTRY.get("nope")
    assert "nope" not in REGISTRY
    assert REGISTRY.names() == ["alpha", "beta"]


def test_test_services_are_not_in_default_registry():
    assert "alpha" not in DEFAULT_REGISTRY
    assert "beta" not in DEFAULT_REGISTRY
    assert "noscope" not in DEFAULT_REGISTRY


def test_read_only_marker():
    def plain():
        pass

    def read():
        pass

    assert read_only(read) is read
    assert is_read_only(read)
    assert not is_read_only(plain)


@pytest.mark.parametrize("name", sorted(ERROR_TYPES))
def test_error_payload_round_trip(name):
    cls = ERROR_TYPES[name]
    payload = to_payload(cls("went wrong"))
    assert payload == ErrorPayload(type=name, message="went wrong")

    restored = from_payload(payload)
    assert type(restored) is cls
    assert restored.message == "went wrong"
    assert str(restored) == "went wrong"


def test_unknown_error_type_name_raises_value_error():
    with pytest.raises(ValueError, match="PermissionDenied"):
        from_payload(ErrorPayload(type="PermissionDenied", message=""))


def test_dispatch_commits_and_closes_on_success(dispatcher, sessions):
    reply = dispatcher.dispatch(envelope("alpha", "put", item_id=1, value="new"))

    assert reply == Reply(result=Item(id=1, value="new"))
    assert CALLS == [("alpha", "put", {"item_id": 1, "value": "new"})]
    assert [session.log for session in sessions.sessions] == [["commit", "close"]]


def test_dispatch_rolls_back_and_replies_with_error_on_mesh_error(dispatcher, sessions):
    reply = dispatcher.dispatch(envelope("alpha", "missing", item_id=1))

    assert reply == Reply(error=ErrorPayload(type="NotFound", message="item 1 not found"))
    assert [session.log for session in sessions.sessions] == [["rollback", "close"]]


def test_dispatch_rolls_back_and_reraises_other_exceptions(dispatcher, sessions):
    with pytest.raises(RuntimeError, match="boom"):
        dispatcher.dispatch(envelope("alpha", "boom"))

    assert CALLS == [("alpha", "boom", {})]
    assert [session.log for session in sessions.sessions] == [["rollback", "close"]]


def test_dispatch_unknown_service_replies_without_opening_a_session(dispatcher, sessions, meshes):
    reply = dispatcher.dispatch(envelope("nope", "get", item_id=1))

    assert reply.result is None
    assert reply.error.type == "UnknownService"
    assert "nope" in reply.error.message
    assert sessions.sessions == []
    assert meshes.built == []
    assert CALLS == []


def test_dispatch_binds_service_to_envelope_user_and_mesh(sessions, meshes):
    seen = []
    registry = ServiceRegistry()

    class Probe(Alpha, registry=registry):
        name = "probe"

        def whoami(self) -> dict:
            seen.append(self)
            return {"user_id": self.user_id}

    reply = Dispatcher(registry, sessions, meshes).dispatch(envelope("probe", "whoami", user_id=8))

    assert reply == Reply(result={"user_id": 8})
    [service] = seen
    [(built_for, mesh)] = meshes.built
    assert built_for == 8
    assert service.mesh is mesh
    assert service.user_id == 8
    [session] = sessions.sessions
    assert service.session is session


def test_dispatch_rehydrates_arguments_against_the_hints(dispatcher, sessions, meshes):
    reply = dispatcher.dispatch(envelope("alpha", "echo_update", data={"title": "x"}))

    assert type(reply.result) is schemas.MeetingUpdate
    received = CALLS[-1][2]["data"]
    assert type(received) is schemas.MeetingUpdate
    assert received.model_fields_set == {"title"}
    assert [session.log for session in sessions.sessions] == [["commit", "close"]]


def test_dispatch_validation_error_opens_no_session(dispatcher, sessions):
    with pytest.raises(ValidationError):
        dispatcher.dispatch(envelope("alpha", "get", item_id="x"))

    assert sessions.sessions == []
    assert CALLS == []


def test_update_crosses_with_only_the_set_fields(parts):
    result = parts.build_mesh(7).alpha.echo_update(data=schemas.MeetingUpdate(title="x"))

    received = CALLS[-1][2]["data"]
    assert type(received) is schemas.MeetingUpdate
    assert received.model_fields_set == {"title"}
    assert type(result) is schemas.MeetingUpdate
    assert result.model_fields_set == {"title"}
    assert result.description is None


def test_datetime_enum_list_and_none_results_keep_their_types(parts):
    mesh = parts.build_mesh(7)
    when = datetime(2026, 10, 19, 12, tzinfo=timezone.utc)

    returned = mesh.alpha.echo_when(when=when)
    assert returned == when
    assert returned.tzinfo is not None

    assert mesh.alpha.echo_status(status=schemas.TaskStatus.pending) is schemas.TaskStatus.pending
    assert CALLS[-1] == ("alpha", "echo_status", {"status": schemas.TaskStatus.pending})
    assert CALLS[-1][2]["status"] is schemas.TaskStatus.pending

    items = mesh.alpha.items()
    assert items == [Item(id=1, value="a"), Item(id=2, value="b")]
    assert all(type(item) is Item for item in items)

    assert mesh.alpha.nothing() is None


def test_memory_returns_the_callers_instance_and_json_an_equal_copy():
    data = schemas.MeetingUpdate(title="x")

    assert MeshParts("memory").build_mesh(7).alpha.echo_update(data=data) is data

    copy = MeshParts("json").build_mesh(7).alpha.echo_update(data=data)
    assert copy == data
    assert copy is not data
    assert copy.model_fields_set == {"title"}


def test_cache_hit_serves_the_type_the_transport_built(parts):
    mesh = parts.build_mesh(7)

    first = mesh.alpha.get(item_id=1)
    second = mesh.alpha.get(item_id=1)

    assert type(first) is Item
    assert second is first
    assert len(parts.transport.envelopes) == 1
    assert CALLS == [("alpha", "get", {"item_id": 1})]


def test_sibling_write_is_committed_when_the_caller_raises(parts):
    with pytest.raises(NotFound, match="item 1 not found"):
        parts.build_mesh(7).alpha.put_via_beta(item_id=1, value="v")

    assert [session.log for session in parts.sessions.sessions] == [["rollback", "close"], ["commit", "close"]]
    assert parts.sent() == [(7, "alpha", "put_via_beta"), (7, "beta", "put")]


def test_proxy_call_produces_triple_envelope_with_user_id(parts):
    result = parts.build_mesh(7).alpha.get(item_id=1)

    assert result == Item(id=1, value="alpha 1")
    assert parts.transport.envelopes == [
        CallEnvelope(user_id=7, service="alpha", method="get", arguments={"item_id": 1})
    ]


def test_proxy_rejects_positional_arguments(parts):
    with pytest.raises(TypeError):
        parts.build_mesh(7).alpha.get(1)

    assert parts.transport.envelopes == []


def test_unknown_service_attribute_raises_unknown_service(parts):
    with pytest.raises(UnknownService, match="missing"):
        parts.build_mesh(7).missing  # noqa: B018

    assert parts.transport.envelopes == []


def test_underscore_attribute_raises_attribute_error(parts):
    mesh = parts.build_mesh(7)
    with pytest.raises(AttributeError):
        mesh._nope  # noqa: B018
    with pytest.raises(AttributeError):
        mesh.alpha._private  # noqa: B018
    with pytest.raises(AttributeError):
        mesh.alpha.not_a_method  # noqa: B018


def test_call_with_no_user_raises_no_user_before_transport(parts):
    with pytest.raises(NoUser):
        ServiceMesh(None, REGISTRY, parts.transport, parts.cache, ToolRegistry(REGISTRY))

    mesh = parts.build_mesh(7)
    mesh.user_id = None
    with pytest.raises(NoUser):
        mesh.alpha.get(item_id=1)

    assert parts.transport.envelopes == []
    assert CALLS == []


def test_read_is_cached_per_user(parts):
    first = parts.build_mesh(7).alpha.get(item_id=1)
    second = parts.build_mesh(7).alpha.get(item_id=1)
    other = parts.build_mesh(8).alpha.get(item_id=1)

    assert first == second == other == Item(id=1, value="alpha 1")
    assert parts.sent() == [(7, "alpha", "get"), (8, "alpha", "get")]
    assert CALLS == [("alpha", "get", {"item_id": 1})] * 2


def warm(parts, service: str, user_ids=(7, 8)) -> None:
    for user_id in user_ids:
        getattr(parts.build_mesh(user_id), service).get(item_id=1)


def cached(parts, service: str, user_id: int) -> bool:
    return parts.cache.get(cache_key(service, "get", user_id, {"item_id": 1})) is not None


def test_write_clears_own_and_declared_services_for_every_user(parts):
    warm(parts, "alpha")
    warm(parts, "beta")

    parts.build_mesh(7).beta.put(item_id=1, value="new")

    assert [cached(parts, "alpha", user_id) for user_id in (7, 8)] == [True, True]
    assert [cached(parts, "beta", user_id) for user_id in (7, 8)] == [False, False]

    warm(parts, "beta")
    parts.build_mesh(8).alpha.put(item_id=1, value="new")

    assert [cached(parts, service, user_id) for service in ("alpha", "beta") for user_id in (7, 8)] == [False] * 4


def test_undecorated_method_is_never_cached(parts):
    mesh = parts.build_mesh(7)
    mesh.alpha.peek(item_id=1)
    mesh.alpha.peek(item_id=1)

    assert CALLS == [("alpha", "peek", {"item_id": 1})] * 2
    assert parts.sent() == [(7, "alpha", "peek")] * 2


def test_cached_read_expires_after_ttl(parts):
    mesh = parts.build_mesh(7)
    mesh.alpha.get(item_id=1)
    mesh.alpha.get(item_id=1)
    assert len(CALLS) == 1

    parts.clock.now = 60.5
    mesh.alpha.get(item_id=1)

    assert CALLS == [("alpha", "get", {"item_id": 1})] * 2


def test_sibling_call_goes_through_transport_and_cache_with_same_user(parts):
    mesh = parts.build_mesh(7)
    result = mesh.beta.get_via_alpha(item_id=1)

    assert result == Item(id=1, value="alpha 1")
    assert parts.sent() == [(7, "beta", "get_via_alpha"), (7, "alpha", "get")]

    assert mesh.alpha.get(item_id=1) == Item(id=1, value="alpha 1")
    assert len(parts.transport.envelopes) == 2


def test_mesh_error_round_trips_with_message_and_caches_nothing(parts):
    warm(parts, "alpha")
    warm(parts, "beta")
    entries_before = dict(parts.cache._entries)

    with pytest.raises(NotFound, match="item 1 not found") as raised:
        parts.build_mesh(7).alpha.missing(item_id=1)

    assert raised.value.message == "item 1 not found"
    assert parts.cache._entries == entries_before
    assert [session.log for session in parts.sessions.sessions][-1] == ["rollback", "close"]


def test_none_result_is_not_cached(parts):
    mesh = parts.build_mesh(7)
    assert mesh.alpha.nothing() is None
    assert mesh.alpha.nothing() is None

    assert CALLS == [("alpha", "nothing", {})] * 2
    assert parts.cache._entries == {}


def test_build_runtime_rejects_unknown_transport():
    with pytest.raises(ValueError, match="MESH_TRANSPORT='grpc' is not supported; expected one of: memory"):
        build_runtime({"MESH_TRANSPORT": "grpc"}, registry=REGISTRY, session_factory=FakeSessionFactory())


def test_build_runtime_rejects_unknown_cache():
    with pytest.raises(ValueError, match="MESH_CACHE='redis' is not supported; expected one of: memory"):
        build_runtime({"MESH_CACHE": "redis"}, registry=REGISTRY, session_factory=FakeSessionFactory())


@pytest.mark.parametrize("ttl", ["abc", "0", "-1", "nan", ""])
def test_build_runtime_rejects_bad_ttl(ttl):
    with pytest.raises(ValueError, match="MESH_CACHE_TTL_SECONDS") as raised:
        build_runtime({"MESH_CACHE_TTL_SECONDS": ttl}, registry=REGISTRY, session_factory=FakeSessionFactory())

    assert repr(ttl) in str(raised.value)


def test_build_runtime_defaults_to_memory_with_sixty_second_ttl():
    runtime = build_runtime({}, registry=REGISTRY, session_factory=FakeSessionFactory())

    assert isinstance(runtime, MeshRuntime)
    assert runtime.registry is REGISTRY
    assert isinstance(runtime.dispatcher, Dispatcher)
    assert isinstance(runtime.transport, TransportRouter)
    assert runtime.routes == {"alpha": "memory", "beta": "memory"}
    assert list(runtime.transports) == ["memory"]
    assert isinstance(runtime.transports["memory"], InMemoryTransport)
    assert isinstance(runtime.cache, InMemoryCache)
    assert runtime.cache.ttl_seconds == 60


def test_build_runtime_reads_ttl_from_env():
    runtime = build_runtime({"MESH_CACHE_TTL_SECONDS": "5"}, registry=REGISTRY, session_factory=FakeSessionFactory())

    assert runtime.cache.ttl_seconds == 5


def test_meshes_from_one_runtime_share_the_cache_per_user():
    runtime = build_runtime({}, registry=REGISTRY, session_factory=FakeSessionFactory())

    runtime.build_mesh(7).alpha.get(item_id=1)
    runtime.build_mesh(7).alpha.get(item_id=1)
    assert CALLS == [("alpha", "get", {"item_id": 1})]

    runtime.build_mesh(8).alpha.get(item_id=1)
    assert CALLS == [("alpha", "get", {"item_id": 1})] * 2
    with pytest.raises(NoUser):
        runtime.build_mesh(None)


def test_json_transport_returns_a_dict_result_and_the_far_side_an_int(dispatcher):
    reply = JsonTransport(dispatcher).send(envelope("alpha", "get", item_id=1))

    assert reply == Reply(result={"id": 1, "value": "alpha 1"})
    assert type(reply.result) is dict
    assert CALLS == [("alpha", "get", {"item_id": 1})]
    assert type(CALLS[0][2]["item_id"]) is int


def test_json_transport_carries_an_error_reply_and_propagates_other_exceptions(dispatcher, sessions):
    transport = JsonTransport(dispatcher)

    reply = transport.send(envelope("alpha", "missing", item_id=1))
    assert reply == Reply(error=ErrorPayload(type="NotFound", message="item 1 not found"))
    assert reply.result is None

    with pytest.raises(RuntimeError, match="boom"):
        transport.send(envelope("alpha", "boom"))
    assert sessions.sessions[-1].log == ["rollback", "close"]


def test_build_runtime_json_builds_one_json_transport_behind_a_router():
    runtime = build_runtime({"MESH_TRANSPORT": "json"}, registry=REGISTRY, session_factory=FakeSessionFactory())

    assert runtime.routes == {"alpha": "json", "beta": "json"}
    assert list(runtime.transports) == ["json"]
    assert isinstance(runtime.transports["json"], JsonTransport)
    assert isinstance(runtime.transport, TransportRouter)
    assert runtime.build_mesh(7).alpha.get(item_id=1) == Item(id=1, value="alpha 1")


def test_build_runtime_override_routes_one_service():
    runtime = build_runtime({"MESH_TRANSPORT_ALPHA": "json"}, registry=REGISTRY, session_factory=FakeSessionFactory())

    assert runtime.routes == {"alpha": "json", "beta": "memory"}
    assert sorted(runtime.transports) == ["json", "memory"]

    runtime = build_runtime(
        {"MESH_TRANSPORT_ALPHA": "json", "MESH_TRANSPORT_BETA": "json"},
        registry=REGISTRY,
        session_factory=FakeSessionFactory(),
    )
    assert runtime.routes == {"alpha": "json", "beta": "json"}
    assert list(runtime.transports) == ["json"]


def test_build_runtime_rejects_unknown_override_service_and_transport():
    with pytest.raises(
        ValueError,
        match="MESH_TRANSPORT_GAMMA names no registered service; expected one of: MESH_TRANSPORT_ALPHA, "
        "MESH_TRANSPORT_BETA",
    ):
        build_runtime({"MESH_TRANSPORT_GAMMA": "json"}, registry=REGISTRY, session_factory=FakeSessionFactory())

    with pytest.raises(ValueError, match="MESH_TRANSPORT_ALPHA='grpc' is not supported; expected one of: memory, json"):
        build_runtime({"MESH_TRANSPORT_ALPHA": "grpc"}, registry=REGISTRY, session_factory=FakeSessionFactory())


def test_sibling_call_crosses_from_json_to_memory():
    runtime = build_runtime({"MESH_TRANSPORT_ALPHA": "json"}, registry=REGISTRY, session_factory=FakeSessionFactory())
    json_recorder = runtime.transports["json"] = RecordingTransport(runtime.transports["json"])
    memory_recorder = runtime.transports["memory"] = RecordingTransport(runtime.transports["memory"])
    outer_recorder = runtime.transport = RecordingTransport(runtime.transport)

    assert runtime.build_mesh(7).beta.get_via_alpha(item_id=1) == Item(id=1, value="alpha 1")

    def sent(recorder: RecordingTransport) -> list[tuple[int, str, str]]:
        return [(e.user_id, e.service, e.method) for e in recorder.envelopes]

    assert sent(memory_recorder) == [(7, "beta", "get_via_alpha")]
    assert sent(json_recorder) == [(7, "alpha", "get")]
    assert sent(outer_recorder) == [(7, "beta", "get_via_alpha"), (7, "alpha", "get")]
