"""What a service author needs: the registry, the base class, the read-only marker, and the two models derived
from a method's signature."""

from __future__ import annotations

import functools
import inspect
import typing
from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Any, Callable, ClassVar, TypeVar

from pydantic import BaseModel, ConfigDict, create_model
from sqlalchemy.orm import Session
from sqlalchemy.sql import Select

from errors import UnknownService

if TYPE_CHECKING:
    from mesh import ServiceMesh

F = TypeVar("F", bound=Callable)
READ_ONLY_ATTR = "__mesh_read_only__"


def read_only(fn: F) -> F:
    """Only marked methods are cached; @tool sets the same marker."""
    setattr(fn, READ_ONLY_ATTR, True)
    return fn


def is_read_only(fn: Callable) -> bool:
    return getattr(fn, READ_ONLY_ATTR, False)


@functools.cache
def input_model(fn: Callable) -> type[BaseModel]:
    """Memoised on the function, so the decorator and the dispatcher share one class per method; extra arguments
    are forbidden."""
    hints = typing.get_type_hints(fn)
    parameters = list(inspect.signature(fn).parameters.values())
    if parameters and parameters[0].name == "self":
        parameters = parameters[1:]
    fields: dict[str, Any] = {
        p.name: (hints[p.name], ... if p.default is inspect.Parameter.empty else p.default) for p in parameters
    }
    return create_model(f"{fn.__name__} input", __config__=ConfigDict(extra="forbid"), **fields)


@functools.cache
def output_model(fn: Callable) -> type[BaseModel]:
    hint = typing.get_type_hints(fn).get("return", type(None))
    if hint is None:
        hint = type(None)
    return create_model(f"{fn.__name__} output", result=(hint, ...))


class ServiceRegistry:
    def __init__(self) -> None:
        self._services: dict[str, type[BaseService]] = {}

    def register(self, cls: type[BaseService]) -> None:
        name = cls.name
        if not isinstance(name, str) or not name.isidentifier() or name.startswith("_"):
            raise TypeError(f"{cls.__qualname__}.name must be an identifier not starting with '_', got {name!r}")
        if name in self._services:
            raise TypeError(f"service name {name!r} is already registered by {self._services[name].__qualname__}")
        self._services[name] = cls

    def get(self, name: str) -> type[BaseService]:
        try:
            return self._services[name]
        except KeyError:
            raise UnknownService(f"unknown service {name!r}") from None

    def __contains__(self, name: str) -> bool:
        return name in self._services

    def names(self) -> list[str]:
        return sorted(self._services)


DEFAULT_REGISTRY = ServiceRegistry()


class BaseService(ABC):
    name: ClassVar[str | None] = None
    invalidates: ClassVar[tuple[str, ...]] = ()

    def __init_subclass__(cls, registry: ServiceRegistry | None = None, **kwargs) -> None:
        super().__init_subclass__(**kwargs)
        if cls.name is not None:
            (registry if registry is not None else DEFAULT_REGISTRY).register(cls)

    def __init__(self, mesh: ServiceMesh, user_id: int, session: Session) -> None:
        self.mesh = mesh
        self.user_id = user_id
        self.session = session

    @abstractmethod
    def scope(self, stmt: Select) -> Select:
        """Restrict a select over this service's model to what self.user_id may see."""
