"""The in-process service mesh: envelope, reply, transports, dispatcher, the per-request mesh, and the per-process
runtime."""

from __future__ import annotations

import os
from typing import Any, Callable, Mapping, Protocol

from pydantic import BaseModel
from sqlalchemy.orm import Session

from cache import Cache, InMemoryCache, cache_key
from errors import ErrorPayload, MeshError, NoUser, UnknownService, from_payload, to_payload
from services.base import DEFAULT_REGISTRY, ServiceRegistry, input_model, is_read_only, output_model
from tools import ToolDefinition, ToolRegistry, ToolResult


class CallEnvelope(BaseModel):
    """One call as data. It carries the caller's user id and nothing else about them."""

    user_id: int
    service: str
    method: str
    arguments: dict[str, Any]


class Reply(BaseModel):
    """Exactly one of result or error is set. Any passes Python objects through untouched in pydantic 2.5.3."""

    result: Any = None
    error: ErrorPayload | None = None


class Transport(Protocol):
    """Carries one envelope to a dispatcher and brings one reply back."""

    def send(self, envelope: CallEnvelope) -> Reply: ...


class Dispatcher:
    """Runs one service method in one session and packages a MeshError as a reply."""

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
        fn = getattr(cls, envelope.method)
        # Rehydrate before a session exists: a dict becomes the model, an instance passes through, and a bad value
        # is a ValidationError that opens nothing.
        model = input_model(fn)
        validated = model.model_validate(envelope.arguments)
        arguments = {name: getattr(validated, name) for name in model.model_fields}
        session = self._session_factory()
        try:
            service = cls(self._mesh_factory(envelope.user_id), envelope.user_id, session)
            value = getattr(service, envelope.method)(**arguments)
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
    """Hands the envelope object to the dispatcher in the same frame. Nothing is copied."""

    def __init__(self, dispatcher: Dispatcher) -> None:
        self._dispatcher = dispatcher

    def send(self, envelope: CallEnvelope) -> Reply:
        return self._dispatcher.dispatch(envelope)


class JsonTransport:
    """The loopback stand-in for a remote hop: every envelope and reply crosses a JSON string, unset fields excluded
    so a partial update keeps its meaning."""

    def __init__(self, dispatcher: Dispatcher) -> None:
        self._dispatcher = dispatcher

    def send(self, envelope: CallEnvelope) -> Reply:
        received = CallEnvelope.model_validate_json(envelope.model_dump_json(exclude_unset=True))
        reply = self._dispatcher.dispatch(received)
        return Reply.model_validate_json(reply.model_dump_json(exclude_unset=True))


class TransportRouter:
    """Routes each service name to one transport. Both mappings are read at send time, so a test may swap one after
    startup."""

    def __init__(self, transports: Mapping[str, Transport], routes: Mapping[str, str]) -> None:
        self._transports = transports
        self._routes = routes

    def send(self, envelope: CallEnvelope) -> Reply:
        return self._transports[self._routes[envelope.service]].send(envelope)


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
    """One per request, bound to one user id. The parts live under underscore names so that no service name can be
    shadowed."""

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
        """The same list for every user; what a tool returns is what depends on the user."""
        return list(self._tools.definitions)

    def call_tool(self, name: str, arguments: dict[str, Any]) -> ToolResult:
        """UnknownTool raises; a MeshError is a result."""
        return self._tools.call(name, arguments, self.call)

    def call(self, service: str, method: str, arguments: dict[str, Any]) -> Any:
        """The cache is checked before routing and errors never touch it; the result is typed by the method's return
        hint whichever transport carried it."""
        if self.user_id is None:
            raise NoUser("the mesh needs an authenticated user id")
        cls = self._registry.get(service)
        fn = getattr(cls, method)
        read = is_read_only(fn)

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
        result = output_model(fn)(result=reply.result).result

        if read:
            if result is not None:
                self._cache.set(key, result)
        else:
            for name in (service, *cls.invalidates):
                self._cache.clear(name)
        return result


class MeshRuntime:
    """The per-process parts; build_mesh is the per-request step. The dispatcher's mesh factory is build_mesh
    itself, so a far-side service gets a mesh over the same transport and cache."""

    def __init__(
        self,
        registry: ServiceRegistry,
        cache: Cache,
        session_factory: Callable[[], Session],
        routes: Mapping[str, str],
    ) -> None:
        self.registry = registry
        self.cache = cache
        self.dispatcher = Dispatcher(registry, session_factory, self.build_mesh)
        self.routes: dict[str, str] = dict(routes)
        self.transports: dict[str, Transport] = {
            name: TRANSPORT_FACTORIES[name](self.dispatcher) for name in sorted(set(self.routes.values()))
        }
        self.transport: Transport = TransportRouter(self.transports, self.routes)
        self.tools = ToolRegistry(registry)  # once per process, after every service module is imported

    def build_mesh(self, user_id: int) -> ServiceMesh:
        return ServiceMesh(user_id, self.registry, self.transport, self.cache, self.tools)


TRANSPORT_FACTORIES: dict[str, Callable[[Dispatcher], Transport]] = {
    "memory": InMemoryTransport,
    "json": JsonTransport,
}
TRANSPORTS = tuple(TRANSPORT_FACTORIES)  # the first entry is the default
CACHES = ("memory",)
ROUTE_PREFIX = "MESH_TRANSPORT_"


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


def _routes(env: Mapping[str, str], registry: ServiceRegistry) -> dict[str, str]:
    """MESH_TRANSPORT for every service, then MESH_TRANSPORT_<SERVICE> as an override; a bad name or value raises."""
    default = _choice(env, "MESH_TRANSPORT", TRANSPORTS)
    routes = {name: default for name in registry.names()}
    by_variable = {ROUTE_PREFIX + name.upper(): name for name in registry.names()}
    for key in sorted(env):
        if not key.startswith(ROUTE_PREFIX):
            continue
        if key not in by_variable:
            raise ValueError(f"{key} names no registered service; expected one of: {', '.join(by_variable)}")
        routes[by_variable[key]] = _choice(env, key, TRANSPORTS)
    return routes


def build_runtime(
    env: Mapping[str, str] | None = None,
    *,
    registry: ServiceRegistry = DEFAULT_REGISTRY,
    session_factory: Callable[[], Session],
) -> MeshRuntime:
    """Fails on an unknown name or a bad TTL; never falls back silently."""
    env = os.environ if env is None else env
    routes = _routes(env, registry)
    _choice(env, "MESH_CACHE", CACHES)
    return MeshRuntime(registry, InMemoryCache(ttl_seconds=_ttl(env)), session_factory, routes)
