"""The in-process service mesh: envelope, reply, transport, dispatcher, the per-request mesh (D13 to D16), and the
per-process runtime that composes them at startup (D17), including the tool view built once over the registry (D42)."""

from __future__ import annotations

import os
from typing import Any, Callable, Mapping, Protocol

from pydantic import BaseModel
from sqlalchemy.orm import Session

from cache import Cache, InMemoryCache, cache_key
from errors import ErrorPayload, MeshError, NoUser, UnknownService, from_payload, to_payload
from services.base import DEFAULT_REGISTRY, ServiceRegistry, is_read_only
from tools import ToolDefinition, ToolRegistry, ToolResult


class CallEnvelope(BaseModel):
    """One call as data (D33). It carries the caller's id, never a user object (D34)."""

    user_id: int
    service: str
    method: str
    arguments: dict[str, Any]


class Reply(BaseModel):
    """Exactly one of result or error is set. Any passes Python objects through untouched in pydantic 2.5.3."""

    result: Any = None
    error: ErrorPayload | None = None


class Transport(Protocol):
    def send(self, envelope: CallEnvelope) -> Reply: ...


class Dispatcher:
    """Far side of every transport (D32) and the only thing that instantiates a service.

    This is where an inbound adapter (FastAPI now, a gRPC server later) and an outbound transport (in-memory
    now, a gRPC client later) meet (D41). Each dispatch opens one session, commits on a return, rolls back on
    any exception, and closes the session before the reply leaves (D36). A MeshError becomes Reply(error=...);
    anything else is a bug and propagates unwrapped.
    """

    def __init__(
        self,
        registry: ServiceRegistry,
        session_factory: Callable[[], Session],
        mesh_factory: Callable[[int], ServiceMesh],
    ) -> None:
        self._registry = registry
        self._session_factory = session_factory
        self._mesh_factory = mesh_factory

    def dispatch(self, envelope: CallEnvelope) -> Reply:
        try:
            cls = self._registry.get(envelope.service)
        except UnknownService as exc:
            return Reply(error=to_payload(exc))

        session = self._session_factory()
        try:
            service = cls(self._mesh_factory(envelope.user_id), envelope.user_id, session)
            value = getattr(service, envelope.method)(**envelope.arguments)
        except MeshError as exc:
            session.rollback()
            return Reply(error=to_payload(exc))
        except BaseException:
            session.rollback()
            raise
        else:
            session.commit()
            return Reply(result=value)
        finally:
            session.close()


class InMemoryTransport:
    def __init__(self, dispatcher: Dispatcher) -> None:
        self._dispatcher = dispatcher

    def send(self, envelope: CallEnvelope) -> Reply:
        return self._dispatcher.dispatch(envelope)


class ServiceProxy:
    """What mesh.<service> returns. Each public method of the service becomes a keyword-only call into the mesh."""

    def __init__(self, mesh: ServiceMesh, service: str) -> None:
        self._mesh = mesh
        self._service = service

    def __getattr__(self, method: str) -> Callable[..., Any]:
        cls = self._mesh._registry.get(self._service)
        if method.startswith("_") or not callable(getattr(cls, method, None)):
            raise AttributeError(f"service {self._service!r} has no public method {method!r}")

        def _call(**arguments: Any) -> Any:
            return self._mesh.call(self._service, method, arguments)

        return _call


class ServiceMesh:
    """One per request, bound to one user id (D13). A mesh with no user cannot exist (D39).

    The parts live under underscore names so that no service name can be shadowed by an attribute of the mesh.
    get_tools and call_tool are the MCP-shaped face of the same mesh (D25); call_tool routes through call.
    """

    def __init__(
        self, user_id: int, registry: ServiceRegistry, transport: Transport, cache: Cache, tools: ToolRegistry
    ) -> None:
        if user_id is None:
            raise NoUser("the mesh needs an authenticated user id")
        self.user_id = user_id
        self._registry = registry
        self._transport = transport
        self._cache = cache
        self._tools = tools

    def __getattr__(self, name: str) -> ServiceProxy:
        if name.startswith("_"):
            raise AttributeError(name)
        if name not in self._registry:
            raise UnknownService(f"unknown service {name!r}")
        return ServiceProxy(self, name)

    def get_tools(self) -> list[ToolDefinition]:
        """The MCP tool list (D25). The same for every user; what a tool returns is what depends on the user."""
        return list(self._tools.definitions)

    def call_tool(self, name: str, arguments: dict[str, Any]) -> ToolResult:
        """Validate, route through call, answer as an MCP result (D25). UnknownTool raises; a MeshError is a result."""
        return self._tools.call(name, arguments, self.call)

    def call(self, service: str, method: str, arguments: dict[str, Any]) -> Any:
        """The caller's side of every call (D14). The cache sits above routing (D40); errors never touch it."""
        if self.user_id is None:
            raise NoUser("the mesh needs an authenticated user id")
        cls = self._registry.get(service)
        read = is_read_only(getattr(cls, method))

        if read:
            key = cache_key(service, method, self.user_id, arguments)
            cached = self._cache.get(key)
            if cached is not None:
                return cached

        reply = self._transport.send(
            CallEnvelope(user_id=self.user_id, service=service, method=method, arguments=arguments)
        )
        if reply.error is not None:
            raise from_payload(reply.error)

        if read:
            if reply.result is not None:
                self._cache.set(key, reply.result)
        else:
            for name in (service, *cls.invalidates):
                self._cache.clear(name)
        return reply.result


class MeshRuntime:
    """The per-process parts (D17): registry, cache, dispatcher, transport, tools. build_mesh is the per-request step.

    The dispatcher's mesh factory is build_mesh itself, so a service on the far side gets a mesh over the same
    transport and cache as its caller (story 17). build_mesh reads the attributes at call time, so a test may
    swap the cache or wrap the transport after construction and every mesh, near or far, sees the swap.
    """

    def __init__(self, registry: ServiceRegistry, cache: Cache, session_factory: Callable[[], Session]) -> None:
        self.registry = registry
        self.cache = cache
        self.dispatcher = Dispatcher(registry, session_factory, self.build_mesh)
        self.transport: Transport = InMemoryTransport(self.dispatcher)
        self.tools = ToolRegistry(registry)  # once per process, after every service module is imported (D42)

    def build_mesh(self, user_id: int) -> ServiceMesh:
        return ServiceMesh(user_id, self.registry, self.transport, self.cache, self.tools)


TRANSPORTS = ("memory",)
CACHES = ("memory",)


def _choice(env: Mapping[str, str], name: str, known: tuple[str, ...]) -> str:
    value = env.get(name, known[0])
    if value not in known:
        raise ValueError(f"{name}={value!r} is not supported; expected one of: {', '.join(known)}")
    return value


def _ttl(env: Mapping[str, str]) -> float:
    raw = env.get("MESH_CACHE_TTL_SECONDS", "60")
    try:
        ttl = float(raw)
    except ValueError:
        ttl = None
    if ttl is None or not ttl > 0:
        raise ValueError(f"MESH_CACHE_TTL_SECONDS={raw!r} is not a positive number of seconds")
    return ttl


def build_runtime(
    env: Mapping[str, str] | None = None,
    *,
    registry: ServiceRegistry = DEFAULT_REGISTRY,
    session_factory: Callable[[], Session],
) -> MeshRuntime:
    """The startup step (stories 48 to 52). Fails on an unknown name or a bad TTL; never falls back silently."""
    env = os.environ if env is None else env
    _choice(env, "MESH_TRANSPORT", TRANSPORTS)
    _choice(env, "MESH_CACHE", CACHES)
    return MeshRuntime(registry, InMemoryCache(ttl_seconds=_ttl(env)), session_factory)
