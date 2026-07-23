"""services — the LLM tool-call parsers the codegen fix shapes share.

The old Services gateway + AgentLoop that owned the synchronous fix loop are gone (a build is now a
chain of llm jobs driven by build_chain). What remains here is the loose-output normalization every
local-model turn depends on: `parse_args` (any argument shape → dict) and `salvage_tool_call`
(rebuild a call from content JSON when it uniquely fits one offered tool).
"""

import pytest

from maestro.services import parse_args, salvage_tool_call

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


# ── salvage_tool_call: recover a tool call from loose content ─────────────────
def test_salvage_unique_match_rebuilds_call():
    # WHY: local models sometimes emit a tool's args as raw JSON in message content instead of a
    # function call. When the keys uniquely fit ONE schema, rebuild the call so the work isn't
    # thrown away on a wasted nudge round-trip.
    tc = salvage_tool_call('{"item_id": "i1", "name": "Key"}', [_ADD_SCHEMA, _OTHER_SCHEMA])
    assert tc is not None
    assert tc["function"]["name"] == "add_item"


@pytest.mark.parametrize("content", ["not json at all", "", "{}", '{"nope": 1}'])
def test_salvage_junk_returns_none(content):
    # WHY: when content isn't a confident single-tool match (junk, empty, no required keys), salvage
    # must bail (None) and let the nudge path handle it — never guess a tool.
    assert salvage_tool_call(content, [_ADD_SCHEMA, _OTHER_SCHEMA]) is None


def test_salvage_ambiguous_match_returns_none():
    # WHY: if the same keys satisfy TWO schemas, salvaging would pick arbitrarily — bail instead.
    dup = {"type": "function", "function": {
        "name": "add_item_2",
        "parameters": {"type": "object", "properties": {"item_id": {}, "name": {}},
                       "required": ["item_id"]}}}
    assert salvage_tool_call('{"item_id": "i1"}', [_ADD_SCHEMA, dup]) is None
