"""The closed error set the mesh carries as data. Nothing here knows about HTTP; main.py maps the names to status
codes."""

from pydantic import BaseModel


class MeshError(Exception):
    """Services raise the first three; the mesh and dispatcher the next two; the tool registry the sixth."""

    def __init__(self, message: str = ""):
        super().__init__(message)
        self.message = message


class NotFound(MeshError): ...


class Conflict(MeshError): ...


class Invalid(MeshError): ...


class NoUser(MeshError): ...


class UnknownService(MeshError): ...


class UnknownTool(MeshError): ...  # raised on the caller's side by the tool registry; never crosses a transport


class ErrorPayload(BaseModel):
    type: str  # one of the six class names
    message: str


ERROR_TYPES: dict[str, type[MeshError]] = {
    cls.__name__: cls for cls in (NotFound, Conflict, Invalid, NoUser, UnknownService, UnknownTool)
}


def to_payload(exc: MeshError) -> ErrorPayload:
    return ErrorPayload(type=type(exc).__name__, message=exc.message)


def from_payload(payload: ErrorPayload) -> MeshError:
    cls = ERROR_TYPES.get(payload.type)
    if cls is None:
        raise ValueError(f"unknown error type {payload.type!r}; expected one of: {', '.join(ERROR_TYPES)}")
    return cls(payload.message)
