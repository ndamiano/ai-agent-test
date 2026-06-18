import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from llm_clients.openai_compatible_connector import (
    _chat_tools_to_responses, _chat_messages_to_responses_input, _responses_to_chat,
    OpenAICompatibleConnector,
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
        {"role": "assistant", "content": None,
         "tool_calls": [{"id": "c1", "function": {"name": "write_node", "arguments": "{}"}}]},
        {"role": "tool", "tool_call_id": "c1", "content": "{\"ok\": true}"},
    ]
    instructions, items = _chat_messages_to_responses_input(messages)
    assert instructions == "you are X"                      # system → instructions
    assert items[0] == {"role": "user", "content": "make a node"}
    assert items[1] == {"type": "function_call", "call_id": "c1",
                        "name": "write_node", "arguments": "{}"}   # assistant tool_call
    assert items[2] == {"type": "function_call_output", "call_id": "c1",
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
    c = OpenAICompatibleConnector(base_url="http://localhost:1234", api_style="responses")
    assert c._api_root == "http://localhost:1234/v1"            # responses lives at /v1/responses


def test_streaming_disabled_in_responses_mode():
    # callers gate on _streaming_works, so they never attempt streaming (no fail+fallback spam)
    assert OpenAICompatibleConnector(base_url="http://x", api_style="responses")._streaming_works is False
    assert OpenAICompatibleConnector(base_url="http://x", api_style="chat")._streaming_works is True


def test_stream_yields_error_in_responses_mode_for_fallback():
    # defensive: if called directly anyway, it signals fallback rather than hanging
    c = OpenAICompatibleConnector(base_url="http://x", api_style="responses")
    chunks = list(c.generate_with_tools_stream([{"role": "user", "content": "hi"}]))
    assert len(chunks) == 1 and "error" in chunks[0]


def test_reasoning_aliases_on_off_to_effort_enum():
    # LM Studio native-API spellings map to valid Responses effort values, not dropped.
    assert OpenAICompatibleConnector(base_url="http://x", reasoning="on").reasoning == "high"
    assert OpenAICompatibleConnector(base_url="http://x", reasoning="off").reasoning == "none"
    assert OpenAICompatibleConnector(base_url="http://x", reasoning="low").reasoning == "low"
