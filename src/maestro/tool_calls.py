"""Recovering a tool call the model wrote as TEXT.

The server is supposed to parse the model's tool-call syntax and hand back `message.tool_calls`. It
often doesn't — the engine's parser knows one template, the model was tuned on another, or there is
no parser at all — and a perfectly good call arrives sitting in `content`.

Try the known encodings in order, take the first that yields calls the model was actually offered.
Adding an engine or a model family means adding a PARSER here, never a branch at a call site.
Parsers run strictest first; the schema-shaped fallback runs last because it guesses a name from
argument keys and would otherwise shadow an explicit one.

Every parser returns OpenAI-shaped calls: [{"id", "type": "function", "function": {"name",
"arguments"}}] with `arguments` a JSON string.
"""

from __future__ import annotations

import json
import re
from typing import Dict, List, Optional

# Hermes / Qwen / most local finetunes: JSON inside <tool_call> tags, one block per call.
_HERMES_RE = re.compile(r"<tool_call>\s*(\{.*?\})\s*</tool_call>", re.S)
# Llama 3.1's built-in tag, one JSON object after it.
_PYTHON_TAG_RE = re.compile(r"<\|python_tag\|>\s*(\{.*)", re.S)
# Mistral.
_MISTRAL_RE = re.compile(r"\[TOOL_CALLS\]\s*(\[.*?\]|\{.*?\})", re.S)
# DeepSeek's fenced block form.
_DEEPSEEK_RE = re.compile(
    r"<｜tool▁call▁begin｜>\s*(?:function)?\s*<｜tool▁sep｜>\s*([A-Za-z0-9_]+)\s*\n"
    r"```json\s*(\{.*?\})\s*```", re.S)
# Llama-style XML. Each parameter's value runs to the next tag — it is source code, so it is never
# escaped and can never be parsed as JSON.
_XML_FN_RE = re.compile(r"<function\s*=\s*([A-Za-z0-9_]+)\s*>", re.I)
_XML_PARAM_RE = re.compile(
    r"<parameter\s*=\s*([A-Za-z0-9_]+)\s*>(.*?)"
    r"(?=</parameter\s*>|<parameter\s*=|</function|</tool_call|\Z)", re.I | re.S)
# The same idea with attribute syntax (`<invoke name="x"><parameter name="y">`).
_INVOKE_RE = re.compile(r"<invoke\s+name\s*=\s*[\"']([A-Za-z0-9_]+)[\"']\s*>", re.I)
_INVOKE_PARAM_RE = re.compile(
    r"<parameter\s+name\s*=\s*[\"']([A-Za-z0-9_]+)[\"']\s*>(.*?)"
    r"(?=</parameter\s*>|<parameter\s+name|</invoke|\Z)", re.I | re.S)
# A bare fenced or inline object that names itself.
_NAMED_OBJ_RE = re.compile(r"\{[^{}]*\"name\"\s*:\s*\"[A-Za-z0-9_]+\".*\}", re.S)


def _call(name: str, args) -> Dict:
    if isinstance(args, str):
        try:
            args = json.loads(args)
        except json.JSONDecodeError:
            args = {}
    return {"id": "salvaged", "type": "function",
            "function": {"name": name,
                         "arguments": json.dumps(args if isinstance(args, dict) else {},
                                                 ensure_ascii=False)}}


def _from_named_objects(objs: List[Dict]) -> List[Dict]:
    """`{"name": ..., "arguments"|"parameters": {...}}` — the shape every JSON encoding lands on."""
    out = []
    for o in objs:
        if not isinstance(o, dict):
            continue
        name = o.get("name")
        if not isinstance(name, str):
            continue
        args = o.get("arguments")
        if args is None:
            args = o.get("parameters")
        out.append(_call(name, args if args is not None else {}))
    return out


def _loads(raw: str):
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return None


def parse_hermes(content: str, schemas) -> List[Dict]:
    objs = [_loads(m) for m in _HERMES_RE.findall(content)]
    return _from_named_objects([o for o in objs if o])


def parse_python_tag(content: str, schemas) -> List[Dict]:
    m = _PYTHON_TAG_RE.search(content)
    if not m:
        return []
    obj = _loads(m.group(1).strip()) or _loads(_first_object(m.group(1)) or "")
    return _from_named_objects([obj] if obj else [])


def parse_mistral(content: str, schemas) -> List[Dict]:
    m = _MISTRAL_RE.search(content)
    if not m:
        return []
    obj = _loads(m.group(1))
    return _from_named_objects(obj if isinstance(obj, list) else [obj] if obj else [])


def parse_deepseek(content: str, schemas) -> List[Dict]:
    return [_call(name, _loads(raw) or {}) for name, raw in _DEEPSEEK_RE.findall(content)]


def _xml_calls(content: str, fn_re, param_re) -> List[Dict]:
    out = []
    starts = list(fn_re.finditer(content))
    for i, m in enumerate(starts):
        end = starts[i + 1].start() if i + 1 < len(starts) else len(content)
        args = {k: v.strip("\n") for k, v in param_re.findall(content[m.end():end])}
        if args:
            out.append(_call(m.group(1), args))
    return out


def parse_xml_function(content: str, schemas) -> List[Dict]:
    return _xml_calls(content, _XML_FN_RE, _XML_PARAM_RE)


def parse_xml_invoke(content: str, schemas) -> List[Dict]:
    return _xml_calls(content, _INVOKE_RE, _INVOKE_PARAM_RE)


def parse_named_object(content: str, schemas) -> List[Dict]:
    """A self-naming object with no wrapper at all — the last precise form before guessing."""
    for raw in _NAMED_OBJ_RE.findall(content):
        obj = _loads(raw) or _loads(_first_object(raw) or "")
        calls = _from_named_objects([obj] if obj else [])
        if calls:
            return calls
    return []


def parse_by_arg_shape(content: str, schemas) -> List[Dict]:
    """No name anywhere — infer it from the argument keys, and ONLY when exactly one offered tool
    fits. This is a guess, so it runs last and refuses every ambiguous case."""
    from maestro.services import parse_args
    args = parse_args(content) if content else {}
    if not isinstance(args, dict) or not args:
        return []
    keys = set(args)
    matches = []
    for s in schemas or []:
        f = s.get("function", {})
        params = f.get("parameters", {}) or {}
        props = set(params.get("properties", {}) or {})
        required = set(params.get("required", []) or [])
        if required <= keys <= props:
            matches.append(f.get("name"))
    return [_call(matches[0], args)] if len(matches) == 1 else []


# Strictest and most explicit first; the name-guessing fallback last.
PARSERS = (parse_hermes, parse_deepseek, parse_mistral, parse_python_tag,
           parse_xml_function, parse_xml_invoke, parse_named_object, parse_by_arg_shape)


def _first_object(text: str) -> Optional[str]:
    """The first balanced {...} in `text` — for encodings that put prose after the object."""
    depth = start = 0
    in_str = esc = False
    for i, ch in enumerate(text):
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == "{":
            if depth == 0:
                start = i
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return text[start:i + 1]
    return None


def _required_of(schemas) -> Dict[str, set]:
    out = {}
    for s in schemas or []:
        f = s.get("function") or {}
        if f.get("name"):
            out[f["name"]] = set((f.get("parameters") or {}).get("required") or [])
    return out


def _usable(call: Dict, required: Dict[str, set]) -> bool:
    """A recovered call must name an offered tool AND carry that tool's required arguments.

    Half a call is worse than none — it looks like progress, and the model spends its budget on the
    resulting tool errors instead of on the game."""
    name = call["function"]["name"]
    if name not in required:
        return False
    try:
        args = json.loads(call["function"]["arguments"])
    except json.JSONDecodeError:
        return False
    return required[name] <= set(args)


def parse_tool_calls(content: str, schemas) -> List[Dict]:
    """Every call the model wrote as text, or []. The first parser whose calls are ALL usable wins —
    a parser that half-matches is a misparse, not a partial success."""
    if not content:
        return []
    required = _required_of(schemas)
    for parser in PARSERS:
        try:
            calls = parser(content, schemas)
        except Exception:
            continue
        if calls and all(_usable(c, required) for c in calls):
            for i, c in enumerate(calls):
                c["id"] = f"salvaged_{i}"
            return calls
    return []
