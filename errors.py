"""The closed error set the mesh carries as data (D35). Nothing here knows about HTTP; P3 maps the names."""

from pydantic import BaseModel


class MeshError(Exception):
    """Base of the closed error set (D35). Services raise the first three; the mesh and dispatcher the last two."""

    def __init__(self, message: str = ""):
        super().__init__(message)
        self.message = message


class NotFound(MeshError): ...


class Conflict(MeshError): ...


class Invalid(MeshError): ...


class NoUser(MeshError): ...


class UnknownService(MeshError): ...


class ErrorPayload(BaseModel):
    type: str  # one of the five class names
    message: str


ERROR_TYPES: dict[str, type[MeshError]] = {
    cls.__name__: cls for cls in (NotFound, Conflict, Invalid, NoUser, UnknownService)
}


def to_payload(exc: MeshError) -> ErrorPayload:
    return ErrorPayload(type=type(exc).__name__, message=exc.message)


def from_payload(payload: ErrorPayload) -> MeshError:
    cls = ERROR_TYPES.get(payload.type)
    if cls is None:
        raise ValueError(f"unknown error type {payload.type!r}; expected one of: {', '.join(ERROR_TYPES)}")
    return cls(payload.message)
