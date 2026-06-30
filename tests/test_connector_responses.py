import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from llm_clients.openai_compatible_connector import (
    _chat_tools_to_responses, _chat_messages_to_responses_input, _responses_to_chat,
    _responses_stream_to_chat_chunks, OpenAICompatibleConnector,
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


def test_responses_endpoint_derived_from_api_root():
    c = OpenAICompatibleConnector(base_url="http://localhost:1234")
    assert c._api_root == "http://localhost:1234/v1"            # responses lives at /v1/responses
    assert c.api_endpoint == "http://localhost:1234/v1/responses"


def test_reasoning_aliases_on_off_to_effort_enum():
    # LM Studio native-API spellings map to valid Responses effort values, not dropped.
    assert OpenAICompatibleConnector(base_url="http://x", reasoning="on").reasoning == "high"
    assert OpenAICompatibleConnector(base_url="http://x", reasoning="off").reasoning == "none"
    assert OpenAICompatibleConnector(base_url="http://x", reasoning="low").reasoning == "low"


def test_json_mode_maps_to_text_format():
    # JSON/structured output lives under text.format on the Responses API, not response_format.
    c = OpenAICompatibleConnector(base_url="http://x", model="m")
    payload = c._responses_payload([{"role": "user", "content": "hi"}], None,
                                   {"type": "json_object"}, None, stream=False)
    assert payload["text"] == {"format": {"type": "json_object"}}
    assert "response_format" not in payload


def test_stream_events_translate_to_chat_text_deltas():
    events = [
        {"type": "response.created", "response": {"id": "r1"}},
        {"type": "response.output_text.delta", "delta": "Hel"},
        {"type": "response.output_text.delta", "delta": "lo"},
        {"type": "response.completed",
         "response": {"usage": {"input_tokens": 3, "output_tokens": 2, "total_tokens": 5,
                                "output_tokens_details": {"reasoning_tokens": 1}}}},
    ]
    chunks = list(_responses_stream_to_chat_chunks(events))
    assert chunks[0]["id"] == "r1" and chunks[0]["choices"] == []
    text = "".join(c["choices"][0]["delta"].get("content", "")
                    for c in chunks if c["choices"])
    assert text == "Hello"
    final = chunks[-1]
    assert final["choices"][0]["finish_reason"] == "stop"
    assert final["usage"]["completion_tokens"] == 2
    assert final["usage"]["completion_tokens_details"]["reasoning_tokens"] == 1


def test_stream_events_translate_tool_calls():
    # LM Studio streams a reasoning item, then the function_call's full arguments arrive only
    # in the terminal output_item.done — never as arguments.delta — so we read them there.
    events = [
        {"type": "response.output_item.added", "output_index": 0,
         "item": {"type": "reasoning"}},
        {"type": "response.output_item.done", "output_index": 0,
         "item": {"type": "reasoning"}},
        {"type": "response.output_item.added", "output_index": 1,
         "item": {"type": "function_call", "call_id": "c1", "name": "write_node",
                  "arguments": ""}},
        {"type": "response.output_item.done", "output_index": 1,
         "item": {"type": "function_call", "call_id": "c1", "name": "write_node",
                  "arguments": "{\"node_id\":\"s1\"}"}},
        {"type": "response.completed", "response": {"usage": {}}},
    ]
    chunks = list(_responses_stream_to_chat_chunks(events))
    # reconstruct the tool call the way MainAgent's accumulator does
    name, args, call_id = "", "", None
    for c in chunks:
        for choice in c["choices"]:
            for tc in choice["delta"].get("tool_calls", []):
                assert tc["index"] == 0
                call_id = tc.get("id") or call_id
                name += tc["function"].get("name", "")
                args += tc["function"].get("arguments", "")
    assert call_id == "c1"
    assert name == "write_node"
    assert args == "{\"node_id\":\"s1\"}"
    assert chunks[-1]["choices"][0]["finish_reason"] == "tool_calls"
