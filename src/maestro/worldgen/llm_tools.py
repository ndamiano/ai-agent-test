"""Tool definition helpers: decorate a plain function, get a JSON-Schema tool.

Stdlib plus pydantic only.
"""
from __future__ import annotations

import inspect
import types
import typing
from dataclasses import dataclass, field
from typing import Any, Callable

from pydantic import BaseModel

_PY_TO_JSON = {
    str: "string",
    int: "integer",
    float: "number",
    bool: "boolean",
    list: "array",
    dict: "object",
}


@dataclass
class Tool:
    """A callable tool exposed to the model."""

    name: str
    description: str
    parameters: dict[str, Any]
    fn: Callable[..., Any] | None = None
    coercers: dict[str, Callable[[Any], Any]] = field(default_factory=dict)

    def bind(self, arguments: dict[str, Any]) -> dict[str, Any]:
        out = dict(arguments)
        for key, convert in self.coercers.items():
            if key in out:
                out[key] = convert(out[key])
        return out

    def to_json_schema(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }


def _inline_refs(schema: dict[str, Any]) -> dict[str, Any]:
    """Resolve $ref/$defs into a self-contained schema.

    Pydantic emits `$defs` + `$ref` for nested models; many local inference
    servers render tool schemas into the prompt verbatim and never follow a
    `$ref`, so the model would see an empty object. Inline instead.
    """
    defs = schema.pop("$defs", {})

    def walk(node: Any, seen: frozenset[str]) -> Any:
        if isinstance(node, list):
            return [walk(n, seen) for n in node]
        if not isinstance(node, dict):
            return node
        ref = node.get("$ref")
        if isinstance(ref, str) and ref.startswith("#/$defs/"):
            key = ref.split("/")[-1]
            if key in seen:  # recursive model — leave a generic object
                return {"type": "object"}
            target = defs.get(key)
            if target is None:
                return {"type": "object"}
            merged = {**walk(target, seen | {key}), **{k: v for k, v in node.items() if k != "$ref"}}
            return merged
        return {k: walk(v, seen) for k, v in node.items()}

    return walk(schema, frozenset())


def _is_union(origin: Any) -> bool:
    """True for both `typing.Union[X, None]` and PEP 604 `X | None`."""
    return origin is typing.Union or origin is getattr(types, "UnionType", ())


def _strip_titles(node: Any) -> Any:
    """Drop pydantic's auto-generated `title` keys — prompt bloat, no signal."""
    if isinstance(node, list):
        return [_strip_titles(n) for n in node]
    if isinstance(node, dict):
        return {k: _strip_titles(v) for k, v in node.items() if k != "title"}
    return node


def _is_pydantic(annotation: Any) -> bool:
    return inspect.isclass(annotation) and issubclass(annotation, BaseModel)


def _schema_for(annotation: Any) -> dict[str, Any]:
    if annotation is inspect.Parameter.empty:
        return {"type": "string"}
    if _is_pydantic(annotation):
        return _strip_titles(_inline_refs(annotation.model_json_schema()))
    origin = typing.get_origin(annotation)
    if origin is typing.Literal:
        return {"type": "string", "enum": list(typing.get_args(annotation))}
    if origin in (list, set, tuple):
        args = typing.get_args(annotation)
        item = _schema_for(args[0]) if args else {"type": "string"}
        return {"type": "array", "items": item}
    if origin is dict:
        return {"type": "object"}
    if _is_union(origin):  # Optional[X] and PEP 604 `X | None`
        args = [a for a in typing.get_args(annotation) if a is not type(None)]
        if not args:
            return {"type": "string"}
        if len(args) == 1:
            return _schema_for(args[0])
        return {"anyOf": [_schema_for(a) for a in args]}
    return {"type": _PY_TO_JSON.get(annotation, "string")}


def _coercer_for(annotation: Any) -> Callable[[Any], Any] | None:
    """Return a converter turning the model's raw JSON value into `annotation`."""
    if _is_pydantic(annotation):
        def parse(v: Any) -> Any:
            if isinstance(v, str):  # some local models hand a nested object over as a JSON string
                return annotation.model_validate_json(v)
            return annotation.model_validate(v) if isinstance(v, dict) else v

        return parse
    origin = typing.get_origin(annotation)
    if origin in (list, set, tuple):
        args = typing.get_args(annotation)
        item = _coercer_for(args[0]) if args else None
        if item:
            return lambda v: [item(x) for x in v] if isinstance(v, list) else v
    if _is_union(origin):
        args = [a for a in typing.get_args(annotation) if a is not type(None)]
        inner = _coercer_for(args[0]) if len(args) == 1 else None
        if inner:
            return lambda v: v if v is None else inner(v)
    return None


def signature_info(fn: Callable[..., Any]) -> tuple[dict[str, Any], dict[str, Callable[[Any], Any]]]:
    """Build a JSON Schema object and per-argument coercers from a signature."""
    sig = inspect.signature(fn)
    hints = typing.get_type_hints(fn)
    props: dict[str, Any] = {}
    required: list[str] = []
    coercers: dict[str, Callable[[Any], Any]] = {}
    for name, param in sig.parameters.items():
        if name in ("self", "cls") or param.kind in (
            inspect.Parameter.VAR_POSITIONAL,
            inspect.Parameter.VAR_KEYWORD,
        ):
            continue
        annotation = hints.get(name, param.annotation)
        props[name] = _schema_for(annotation)
        c = _coercer_for(annotation)
        if c:
            coercers[name] = c
        if param.default is inspect.Parameter.empty:
            required.append(name)
    schema = {
        "type": "object",
        "properties": props,
        "required": required,
        "additionalProperties": False,
    }
    return schema, coercers


def tool(
    fn: Callable[..., Any] | None = None,
    *,
    name: str | None = None,
    description: str | None = None,
    parameters: dict[str, Any] | None = None,
) -> Any:
    """Decorator turning a function into a Tool.

        @tool
        def get_weather(city: str) -> str:
            '''Get current weather for a city.'''
            return "sunny"

    The decorated object is still callable; `.tool` holds the Tool definition.
    """

    def wrap(f: Callable[..., Any]) -> Callable[..., Any]:
        doc = inspect.getdoc(f) or ""
        inferred, coercers = signature_info(f)
        t = Tool(
            name=name or f.__name__,
            description=description or doc.split("\n\n")[0] or f.__name__,
            parameters=parameters or inferred,
            fn=f,
            coercers=coercers,
        )
        f.tool = t  # type: ignore[attr-defined]
        return f

    return wrap(fn) if fn is not None else wrap


def as_tool(obj: Any) -> Tool:
    """Coerce a Tool, a @tool-decorated function, or a plain function to a Tool."""
    if isinstance(obj, Tool):
        return obj
    t = getattr(obj, "tool", None)
    if isinstance(t, Tool):
        return t
    if callable(obj):
        return tool(obj).tool  # type: ignore[attr-defined]
    raise TypeError(f"cannot interpret {obj!r} as a tool")


__all__ = ["Tool", "tool", "as_tool", "signature_info"]
