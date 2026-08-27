"""Wire formats an inference server might speak, and the translation between them.

The CANONICAL request is the OpenAI chat shape — messages, tools, {choices, usage} back — because
that is what every caller already speaks. Anything else is a dialect, and translating into it is the
WORKER's job: the worker is the only thing that knows what its target actually serves. The control
plane enqueues canonical and gets canonical back, so adding an engine never touches it.

This module is therefore imported by the worker, not only by the control plane.
"""

from __future__ import annotations

from typing import Dict

# Valid `reasoning.effort` values on the Responses API. "off" is NOT valid — it errors; use "none".
REASONING_EFFORTS = {"none", "minimal", "low", "medium", "high", "xhigh"}


def chat_tools_to_responses(tools):
    """OpenAI chat tool schema -> Responses tool schema (flat: no `function` nesting)."""
    out = []
    for t in tools or []:
        fn = t.get("function", t)
        out.append({"type": "function", "name": fn.get("name"),
                    "description": fn.get("description", ""),
                    "parameters": fn.get("parameters", {})})
    return out


def chat_messages_to_responses_input(messages):
    """OpenAI chat messages[] -> (instructions, Responses input[]).

    system -> instructions; assistant tool_calls -> function_call items; role:tool results ->
    function_call_output items; plain text -> role+content items. Stateless: the whole history
    is re-sent each call, matching how the executor rebuilds context (no previous_response_id).
    """
    instructions = None
    items = []
    for m in messages:
        role = m.get("role")
        content = m.get("content")
        if role == "system":
            instructions = f"{instructions}\n\n{content}" if instructions else (content or "")
        elif role == "tool":
            items.append({"type": "function_call_output",
                          "call_id": m.get("tool_call_id"), "output": content or ""})
        elif role == "assistant":
            if content:
                items.append({"type": "message", "role": "assistant",
                              "content": [{"type": "output_text", "text": content}]})
            for tc in m.get("tool_calls") or []:
                fn = tc.get("function", {})
                items.append({"type": "function_call", "call_id": tc.get("id"),
                              "name": fn.get("name"), "arguments": fn.get("arguments", "")})
        else:  # user / other
            items.append({"type": "message", "role": role or "user", "content": content or ""})
    return instructions, items


def responses_to_chat(resp):
    """Responses output[] -> OpenAI chat {choices:[{message}], usage} so callers are unchanged."""
    text_parts, tool_calls = [], []
    for o in resp.get("output", []) or []:
        t = o.get("type")
        if t == "message":
            for c in o.get("content", []) or []:
                if c.get("type") == "output_text":
                    text_parts.append(c.get("text", ""))
        elif t == "function_call":
            tool_calls.append({"id": o.get("call_id"), "type": "function",
                               "function": {"name": o.get("name"),
                                            "arguments": o.get("arguments", "")}})
    msg = {"role": "assistant", "content": "".join(text_parts) or None}
    if tool_calls:
        msg["tool_calls"] = tool_calls
    u = resp.get("usage", {}) or {}
    od = u.get("output_tokens_details", {}) or {}
    usage = {"prompt_tokens": u.get("input_tokens"),
             "completion_tokens": u.get("output_tokens"),
             "total_tokens": u.get("total_tokens"),
             "completion_tokens_details": {"reasoning_tokens": od.get("reasoning_tokens")}}
    return {"choices": [{"message": msg}], "usage": usage, "id": resp.get("id")}




def chat_to_responses_body(body: Dict) -> Dict:
    """A canonical chat request body -> a Responses request body."""
    instructions, items = chat_messages_to_responses_input(body.get("messages") or [])
    out = {"model": body.get("model"), "input": items,
           "temperature": body.get("temperature", 0.7),
           "max_output_tokens": body.get("max_tokens")}
    if instructions:
        out["instructions"] = instructions
    effort = body.get("reasoning")
    if effort in REASONING_EFFORTS:
        out["reasoning"] = {"effort": effort}
    if effort == "none":
        # llama.cpp ignores reasoning.effort "none" — the model thinks anyway and can burn the whole
        # output budget before any text. The chat-template switch actually disables it.
        out["chat_template_kwargs"] = {"enable_thinking": False}
    if body.get("tools"):
        out["tools"] = chat_tools_to_responses(body["tools"])
    if body.get("response_format"):
        out["text"] = {"format": body["response_format"]}
    return out


def chat_body_for_wire(body: Dict) -> Dict:
    """A canonical chat request body -> a Chat Completions request body: identity, with `reasoning`
    spelled the way that endpoint takes it (`reasoning_effort`). Sampling stays a launch flag on
    the target server."""
    out = {k: v for k, v in body.items() if k != "reasoning"}
    if body.get("reasoning") in REASONING_EFFORTS:
        out["reasoning_effort"] = body["reasoning"]
    return out
