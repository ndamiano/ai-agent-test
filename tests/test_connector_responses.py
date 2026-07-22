import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from llm_clients.connector import (
    LLMConnector,
    _chat_messages_to_responses_input,
    _chat_tools_to_responses,
    _responses_to_chat,
)


def test_tools_flattened_for_responses():
    chat = [{"type": "function", "function": {"name": "write_node", "description": "d",
                                              "parameters": {"type": "object"}}}]
    out = _chat_tools_to_responses(chat)
    assert out == [{"type": "function", "name": "write_node", "description": "d",
                    "parameters": {"type": "object"}}]


def test_messages_to_input_maps_roles_and_tool_roundtrip():
    messages = [
        {"role": "system", "content": "you are X"},
        {"role": "user", "content": "make a node"},
        {"role": "assistant", "content": "thinking out loud"},
        {"role": "assistant", "content": None,
         "tool_calls": [{"id": "c1", "function": {"name": "write_node", "arguments": "{}"}}]},
        {"role": "tool", "tool_call_id": "c1", "content": "{\"ok\": true}"},
    ]
    instructions, items = _chat_messages_to_responses_input(messages)
    assert instructions == "you are X"                      # system → instructions
    # plain message items carry explicit type:message — llama.cpp rejects untyped input items
    assert items[0] == {"type": "message", "role": "user", "content": "make a node"}
    assert items[1] == {"type": "message", "role": "assistant",
                        "content": [{"type": "output_text", "text": "thinking out loud"}]}
    assert items[2] == {"type": "function_call", "call_id": "c1",
                        "name": "write_node", "arguments": "{}"}   # assistant tool_call
    assert items[3] == {"type": "function_call_output", "call_id": "c1",
                        "output": "{\"ok\": true}"}                # tool result
    assert all("messages" not in i for i in items)


def test_responses_output_to_chat_shape():
    resp = {
        "id": "r1",
        "output": [
            {"type": "reasoning", "content": []},
            {"type": "message", "role": "assistant",
             "content": [{"type": "output_text", "text": "hi"}]},
            {"type": "function_call", "call_id": "c9", "name": "write_node",
             "arguments": "{\"node_id\":\"s1\"}"},
        ],
        "usage": {"input_tokens": 10, "output_tokens": 5, "total_tokens": 15,
                  "output_tokens_details": {"reasoning_tokens": 2}},
    }
    chat = _responses_to_chat(resp)
    msg = chat["choices"][0]["message"]
    assert msg["content"] == "hi"
    assert msg["tool_calls"] == [{"id": "c9", "type": "function",
                                  "function": {"name": "write_node",
                                               "arguments": "{\"node_id\":\"s1\"}"}}]
    # usage normalized so the existing log line / callers keep working
    assert chat["usage"]["completion_tokens"] == 5
    assert chat["usage"]["completion_tokens_details"]["reasoning_tokens"] == 2


def test_reasoning_aliases_on_off_to_effort_enum():
    # LM Studio native-API spellings map to valid Responses effort values, not dropped.
    assert LLMConnector(reasoning="on").reasoning == "high"
    assert LLMConnector(reasoning="off").reasoning == "none"
    assert LLMConnector(reasoning="low").reasoning == "low"


def test_json_mode_maps_to_text_format():
    # JSON/structured output lives under text.format on the Responses API, not response_format.
    c = LLMConnector(model="m")
    payload = c._responses_payload([{"role": "user", "content": "hi"}], None,
                                   {"type": "json_object"}, None)
    assert payload["text"] == {"format": {"type": "json_object"}}
    assert "response_format" not in payload
