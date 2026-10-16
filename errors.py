"""The closed error set the mesh carries as data (D35): six names, the sixth raised by the tool registry view.
Nothing here knows about HTTP; P3 maps the names."""

from pydantic import BaseModel


class MeshError(Exception):
    """Base of the closed error set (D35), six names. Services raise the first three; the mesh and dispatcher the
    next two; the tool registry view the sixth."""

    def __init__(self, message: str = ""):
        super().__init__(message)
        self.message = message


class NotFound(MeshError): ...


class Conflict(MeshError): ...


class Invalid(MeshError): ...


class NoUser(MeshError): ...


class UnknownService(MeshError): ...


class UnknownTool(MeshError): ...  # raised by the tool registry view for a name it does not hold (P5)


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
