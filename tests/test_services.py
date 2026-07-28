"""services — the LLM tool-call parsers the codegen fix shapes share.

The old Services gateway + AgentLoop that owned the synchronous fix loop are gone (a build is now a
chain of llm jobs driven by build_chain). What remains here is the loose-output normalization every
local-model turn depends on: `parse_args` (any argument shape → dict). Recovering a call the model
wrote as TEXT lives in maestro/tool_calls.py — see test_tool_calls.py.
"""

import pytest

from maestro.services import parse_args

_ADD_SCHEMA = {"type": "function", "function": {
    "name": "add_item",
    "parameters": {"type": "object",
                   "properties": {"item_id": {}, "name": {}},
                   "required": ["item_id"]}}}
_OTHER_SCHEMA = {"type": "function", "function": {
    "name": "write_node",
    "parameters": {"type": "object",
                   "properties": {"node_id": {}, "content": {}},
                   "required": ["node_id", "content"]}}}


# ── parse_args: normalize any argument shape to a dict ────────────────────────
def test_parse_args_handles_dict_json_and_fenced():
    assert parse_args({"a": 1}) == {"a": 1}
    assert parse_args('{"a": 1}') == {"a": 1}
    assert parse_args('```json\n{"a": 1}\n```') == {"a": 1}
    # a dict whose single key IS the json blob (some templates) unwraps
    assert parse_args({'{"a": 1}': ""}) == {"a": 1}


def test_parse_args_unparseable_returns_empty():
    assert parse_args("not json at all") == {}
    assert parse_args("[1, 2, 3]") == {}   # a non-dict json value is not args
