"""The MCP tool surface (D21 to D26, D42): the decorator, the definition shapes, then the registry view."""

from __future__ import annotations

import inspect
import json
from dataclasses import dataclass
from typing import Any, Callable, TypeVar

from pydantic import BaseModel, ConfigDict, ValidationError

from errors import MeshError, UnknownTool
from services.base import ServiceRegistry, input_model, output_model
from services.base import read_only as mark_read_only  # the decorator's read_only argument shadows the name

F = TypeVar("F", bound=Callable)
TOOL_ATTR = "__mesh_tool__"
Send = Callable[[str, str, dict[str, Any]], Any]  # ServiceMesh.call, or a fake in a unit test


@dataclass(frozen=True)
class ToolSpec:
    """What @tool records on a method. The service name is the registry view's to add (D42)."""

    method: str
    title: str
    description: str
    read_only: bool
    destructive: bool
    idempotent: bool
    input_model: type[BaseModel]
    output_model: type[BaseModel]


def tool(*, title: str, read_only: bool, destructive: bool, idempotent: bool) -> Callable[[F], F]:
    """Mark a service method as an MCP tool (D21). Every argument is required so that a tool line states all four
    facts. Refuses at decoration, with TypeError: a name starting with '_', an empty title, a missing docstring,
    read-only with destructive, read-only with not idempotent. Sets the P1 read-only attribute when read_only is
    true (D24). Never wraps the method."""

    def decorate(fn: F) -> F:
        name = fn.__name__
        if name.startswith("_"):
            raise TypeError(f"@tool on {name!r}: a tool name must not start with '_'")
        if not title:
            raise TypeError(f"@tool on {name!r}: title must not be empty")
        description = inspect.getdoc(fn)
        if description is None or not description.strip():
            raise TypeError(f"@tool on {name!r}: the method needs a docstring, which is the tool's description")
        if read_only and destructive:
            raise TypeError(f"@tool on {name!r}: a read-only tool cannot be destructive")
        if read_only and not idempotent:
            raise TypeError(f"@tool on {name!r}: a read-only tool must be idempotent")
        spec = ToolSpec(
            method=name,
            title=title,
            description=description,
            read_only=read_only,
            destructive=destructive,
            idempotent=idempotent,
            input_model=input_model(fn),
            output_model=output_model(fn),
        )
        if read_only:
            mark_read_only(fn)
        setattr(fn, TOOL_ATTR, spec)
        return fn

    return decorate


class ToolAnnotations(BaseModel):
    model_config = ConfigDict(frozen=True)

    readOnlyHint: bool
    destructiveHint: bool
    idempotentHint: bool
    openWorldHint: bool = False  # every tool touches this application's database and nothing else


class ToolDefinition(BaseModel):
    """One entry of the MCP tools/list result, dated 2026-07-28 (D22). Exactly these six fields."""

    model_config = ConfigDict(frozen=True)

    name: str
    title: str
    description: str
    inputSchema: dict[str, Any]
    outputSchema: dict[str, Any]
    annotations: ToolAnnotations


class TextContent(BaseModel):
    model_config = ConfigDict(frozen=True)

    type: str = "text"
    text: str


class ToolResult(BaseModel):
    """An MCP tools/call result. isError is always present; structuredContent only on a success."""

    model_config = ConfigDict(frozen=True)

    content: list[TextContent]
    structuredContent: dict[str, Any] | None = None
    isError: bool


class ToolList(BaseModel):
    """The GET /tools body, under the key the MCP tools/list result uses."""

    tools: list[ToolDefinition]


class ToolRegistry:
    """The view over a ServiceRegistry (D42): built once at startup, ordered by name, never changed afterwards."""

    def __init__(self, registry: ServiceRegistry) -> None:
        definitions: list[ToolDefinition] = []
        self._specs: dict[str, tuple[str, ToolSpec]] = {}  # name -> (service, spec)
        for service in registry.names():
            cls = registry.get(service)
            for attr in dir(cls):
                spec = None if attr.startswith("_") else getattr(getattr(cls, attr), TOOL_ATTR, None)
                if not isinstance(spec, ToolSpec):
                    continue
                name = f"{cls.name}.{spec.method}"
                self._specs[name] = (service, spec)
                definitions.append(
                    ToolDefinition(
                        name=name,
                        title=spec.title,
                        description=spec.description,
                        inputSchema=spec.input_model.model_json_schema(),
                        outputSchema=spec.output_model.model_json_schema(),
                        annotations=ToolAnnotations(
                            readOnlyHint=spec.read_only,
                            destructiveHint=spec.destructive,
                            idempotentHint=spec.idempotent,
                        ),
                    )
                )
        self.definitions: tuple[ToolDefinition, ...] = tuple(sorted(definitions, key=lambda d: d.name))

    def get(self, name: str) -> tuple[str, ToolSpec]:
        """(service, spec) for a tool name, or UnknownTool. Raises because an unknown name is a caller's bug."""
        try:
            return self._specs[name]
        except KeyError:
            raise UnknownTool(f"unknown tool {name!r}") from None

    def call(self, name: str, arguments: dict[str, Any], send: Send) -> ToolResult:
        """Validate, send, wrap (D25). ValidationError and MeshError are results with isError true; anything else
        propagates (D35). The validated field values are what send receives, so a read builds the HTTP route's
        cache key."""
        service, spec = self.get(name)
        try:
            validated = spec.input_model.model_validate(arguments)
        except ValidationError as exc:
            lines = [f"{'.'.join(str(part) for part in error['loc'])}: {error['msg']}" for error in exc.errors()]
            return ToolResult(content=[TextContent(text="\n".join(lines))], isError=True)
        try:
            value = send(
                service, spec.method, {field: getattr(validated, field) for field in spec.input_model.model_fields}
            )
        except MeshError as exc:
            return ToolResult(content=[TextContent(text=exc.message)], isError=True)
        structured = spec.output_model(result=value).model_dump(mode="json")
        return ToolResult(
            content=[TextContent(text=json.dumps(structured))], structuredContent=structured, isError=False
        )
