# The transport seam

## What the seam is

This service mesh is in-process (D3). It has five parts: a registry of service classes, a per-request mesh bound
to one user id, a cache, one dispatcher, and a transport between the mesh and the dispatcher. A caller writes
`mesh.meetings.get(meeting_id)`. The mesh checks the cache, builds a `CallEnvelope`, and a router picks the
transport for the envelope's service. The transport carries the envelope to the dispatcher, which opens a session,
builds the service, invokes the method, and returns a `Reply`. Two roles meet at the dispatcher (D41). Inbound
adapters sit above it and turn a request from outside into a call. Outbound transports sit below the mesh and
carry a call from the mesh to the dispatcher.

```
  inbound adapters        FastAPI routes now          a gRPC server later
                                     |
                                     v
                          ServiceMesh (bound to one user id) + cache
                                     |
                                     v
                          TransportRouter (service name -> transport)
                                     |
  outbound transports     InMemoryTransport, JsonTransport now, GrpcTransport later
                                     |
                                     v
                          Dispatcher -> session -> service method
```

```
MESH_TRANSPORT=memory|json              default for every service; unset means memory
MESH_TRANSPORT_MEETINGS=memory|json     override for meetings only
MESH_TRANSPORT_NOTES=memory|json        override for notes only
MESH_TRANSPORT_TASKS=memory|json        override for tasks only
MESH_TRANSPORT_USERS=memory|json        override for users only
```

The routing table maps every registered service name to one transport name. `MESH_TRANSPORT` sets the default and
`MESH_TRANSPORT_<SERVICE>`, with the registry name upper-cased, overrides it for one service. The runtime builds
one transport instance per distinct name, and every instance wraps the same dispatcher, so a service reached over
JSON that calls a sibling routed to memory has that call carried in memory. A transport name outside `memory` and
`json`, or an override that names no registered service, fails at startup. Nothing falls back silently.

## The envelope and the reply on the wire

`JsonTransport` renders each envelope and reply with `model_dump_json`, sends the string, and parses it back on
the other side. An envelope for a partial update holds only the one field the caller set. A reply carrying a
`MeetingWithDetails` renders datetimes as ISO 8601 with a `Z` suffix. A reply carrying an error holds the
`ErrorPayload`, and the HTTP adapter turns it into the same body under either transport.

```json
{
  "user_id": 7,
  "service": "meetings",
  "method": "update",
  "arguments": {"meeting_id": 1, "data": {"title": "Planning"}}
}
```

```json
{
  "result": {
    "title": "Planning",
    "description": null,
    "scheduled_time": "2026-10-19T12:00:00Z",
    "id": 1,
    "created_at": "2026-10-19T12:00:00Z",
    "created_by_id": 7,
    "attendees": [{"name": "Ada", "email": "ada@example.com", "id": 7}],
    "notes": [],
    "tasks": []
  },
  "error": null
}
```

```json
{"result": null, "error": {"type": "NotFound", "message": "meeting 7 not found"}}
```

```json
{"detail": "meeting 7 not found"}
```

Three rules make a transport invisible to services and callers. First, only schemas and primitives cross (D33).
An ORM row, a session, or a model never goes into an envelope or a reply. Second, unset fields are excluded on
the way out (`exclude_unset`), so a partial update keeps its meaning. `MeetingUpdate` and `TaskUpdate` default
every field to `None`, and a body that wrote every field would set the description to `None` on the far side.
Third, both sides rehydrate against the method's type hints. The dispatcher rehydrates the arguments before a
session opens, and `ServiceMesh.call` rehydrates the result before the cache stores it. So neither a service nor a
caller sees a dict, and the cache holds the same type whichever transport filled it. The user id is the only
identity that crosses, and the far side reloads nothing (D34).

## Errors

The closed set of `MeshError` classes crosses as an `ErrorPayload`, and the near side raises the same class with
the same message. The gRPC status column is for a client outside this codebase; inside it, a gRPC transport would
carry the error as a reply message, as the next section says.

| Error          | HTTP | gRPC status                  | Note                                              |
|----------------|------|------------------------------|---------------------------------------------------|
| NotFound       | 404  | NOT_FOUND (5)                | Hidden and missing are the same answer (D9)       |
| Conflict       | 409  | ALREADY_EXISTS (6)           | Duplicate email on registration                   |
| Invalid        | 422  | INVALID_ARGUMENT (3)         |                                                   |
| NoUser         | 401  | UNAUTHENTICATED (16)         | Raised before any transport is reached (D39)      |
| UnknownService | 500  | UNIMPLEMENTED (12)           | A bug in the caller, not a client error           |
| UnknownTool    | 500  | never crosses a transport    | Raised on the caller's side by the registry view  |
| anything else  | 500  | INTERNAL (13)                | Propagates unwrapped; never a reply; not retried  |

Anything outside the closed set propagates unwrapped from the dispatcher and is never a reply (D35). A
`ValidationError` raised on the far side, for example when arguments fail to rehydrate, is such a case.

## What a gRPC transport would contain

One `.proto` file with `CallEnvelope` and `Reply` as messages. Their `arguments` and `result` fields would be
`google.protobuf.Struct` or JSON-encoded `string` fields, since the wire shapes above are already JSON. The file
would declare one service with one unary `Send` rpc. `GrpcTransport(channel)` would live in `mesh.py` as a third
`TRANSPORT_FACTORIES` entry. Its `send` would serialise the envelope, call the stub, and parse the reply. A
`MeshError` reply would be carried as a response message, not as a gRPC status, so the near side raises the same
class it raises today. A server shell, one module, would open a listener, and its `Send` handler would parse the
message, call `dispatcher.dispatch`, and serialise the reply. Proto3 optional fields would carry the unset-field
distinction that `exclude_unset` carries in JSON. The one dependency, `grpcio`, and the generated stubs are both
out of scope for this codebase (D2, D16).

## What would not change

Adding a gRPC transport would not change any service, `ServiceMesh`, the cache, the dispatcher, the tool registry,
or the HTTP routes. Every test that uses the `runtime` fixture would stay as it is; the fixture would gain a third
parameter, and each of those tests would run once more over gRPC.
