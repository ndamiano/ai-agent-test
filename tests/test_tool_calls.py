"""Recovering a tool call the model wrote as TEXT, across the encodings engines fail to parse."""
import json

import pytest

from maestro.tool_calls import PARSERS, parse_tool_calls

SCHEMAS = [
    {"type": "function", "function": {
        "name": "write_file", "parameters": {
            "type": "object", "properties": {"path": {}, "content": {}},
            "required": ["path", "content"]}}},
    {"type": "function", "function": {
        "name": "read_file", "parameters": {
            "type": "object", "properties": {"path": {}},
            "required": ["path"]}}},
    {"type": "function", "function": {
        "name": "done", "parameters": {
            "type": "object", "properties": {"summary": {}}, "required": ["summary"]}}},
]

HTML = "<!DOCTYPE html>\n<html><body><h1>Hi</h1></body></html>"

# One row per encoding a local engine has been seen to hand back unparsed.
ENCODINGS = {
    "hermes_qwen": '<tool_call>\n{"name": "write_file", "arguments": {"path": "index.html", '
                   '"content": "<!DOCTYPE html>\\n<html><body><h1>Hi</h1></body></html>"}}\n</tool_call>',
    "llama_python_tag": '<|python_tag|>{"name": "write_file", "parameters": {"path": "index.html", '
                        '"content": "<!DOCTYPE html>\\n<html><body><h1>Hi</h1></body></html>"}}',
    "mistral": '[TOOL_CALLS] [{"name": "write_file", "arguments": {"path": "index.html", '
               '"content": "<!DOCTYPE html>\\n<html><body><h1>Hi</h1></body></html>"}}]',
    "bare_named_object": 'Here is the call:\n```json\n{"name": "write_file", "arguments": '
                         '{"path": "index.html", "content": '
                         '"<!DOCTYPE html>\\n<html><body><h1>Hi</h1></body></html>"}}\n```',
    "xml_function": "Let me write it.\n<tool_call>\n<function=write_file>\n<parameter=path>\n"
                    "index.html\n</parameter>\n<parameter=content>\n" + HTML +
                    "\n</parameter>\n</function>\n</tool_call>",
    "xml_invoke": '<invoke name="write_file">\n<parameter name="path">index.html</parameter>\n'
                  '<parameter name="content">' + HTML + "</parameter>\n</invoke>",
}


@pytest.mark.parametrize("name", sorted(ENCODINGS))
def test_every_encoding_recovers_the_same_call(name):
    calls = parse_tool_calls(ENCODINGS[name], SCHEMAS)
    assert len(calls) == 1, name
    assert calls[0]["function"]["name"] == "write_file"
    args = json.loads(calls[0]["function"]["arguments"])
    assert args["path"] == "index.html"
    assert args["content"].strip() == HTML


def test_the_deepseek_fenced_form():
    content = ("<｜tool▁call▁begin｜>function<｜tool▁sep｜>read_file\n"
               '```json\n{"path": "game.js"}\n```')
    calls = parse_tool_calls(content, SCHEMAS)
    assert len(calls) == 1
    assert calls[0]["function"]["name"] == "read_file"
    assert json.loads(calls[0]["function"]["arguments"]) == {"path": "game.js"}


def test_several_calls_in_one_message_all_land():
    content = ('<tool_call>\n{"name": "read_file", "arguments": {"path": "a.js"}}\n</tool_call>\n'
               '<tool_call>\n{"name": "read_file", "arguments": {"path": "b.js"}}\n</tool_call>')
    calls = parse_tool_calls(content, SCHEMAS)
    assert [json.loads(c["function"]["arguments"])["path"] for c in calls] == ["a.js", "b.js"]
    assert len({c["id"] for c in calls}) == 2      # ids must be distinct for the transcript


def test_argument_shape_infers_a_name_when_exactly_one_tool_fits():
    calls = parse_tool_calls('{"summary": "the game is finished"}', SCHEMAS)
    assert len(calls) == 1 and calls[0]["function"]["name"] == "done"


def test_an_ambiguous_argument_shape_is_refused():
    """`{"path": ...}` fits read_file; guessing a name from keys must never pick when unsure."""
    two_fit = [SCHEMAS[1], {"type": "function", "function": {
        "name": "delete_file", "parameters": {"type": "object", "properties": {"path": {}},
                                              "required": ["path"]}}}]
    assert parse_tool_calls('{"path": "game.js"}', two_fit) == []


def test_a_call_naming_a_tool_that_was_not_offered_is_refused():
    """A parser that half-matches is a misparse, not a partial success."""
    content = '<tool_call>\n{"name": "rm_rf", "arguments": {"path": "/"}}\n</tool_call>'
    assert parse_tool_calls(content, SCHEMAS) == []


def test_prose_alone_yields_nothing():
    assert parse_tool_calls("I will now write the game. Give me a moment.", SCHEMAS) == []
    assert parse_tool_calls("", SCHEMAS) == []
    assert parse_tool_calls(None, SCHEMAS) == []


def test_code_containing_tags_is_not_mistaken_for_a_call():
    """The game's own source is full of angle brackets — only a real call shape counts."""
    assert parse_tool_calls("The file has <parameter> and <function> in a string.", SCHEMAS) == []


def test_no_parser_raises_on_garbage():
    """parse_tool_calls swallows a parser exception, but a parser that throws on ordinary text is a
    bug — assert the whole registry is total over junk."""
    junk = ['<tool_call>{"name":', "<|python_tag|>{", "[TOOL_CALLS] [", "<function=>", "{{{{",
            "<invoke name=", "```json\n{\n"]
    for parser in PARSERS:
        for text in junk:
            parser(text, SCHEMAS)


def test_the_measured_ninfer_failure_recovers():
    """The exact reply ninfer/Qwen3.6-35B-A3B repeated 60 times to the turn cap."""
    content = ("Let me start completely fresh with a clean approach. I'll write the HTML file "
               "properly first.\n\n<tool_call>\n<function=write_file>\n<parameter=content>\n"
               "<!DOCTYPE html>\n<html>\n<head><title>Deck</title></head>\n"
               "<body><div id=\"game\"></div></body>\n</html>\n</parameter>\n"
               "<parameter=path>\nindex.html\n</parameter>\n</function>\n</tool_call>")
    calls = parse_tool_calls(content, SCHEMAS)
    assert len(calls) == 1 and calls[0]["function"]["name"] == "write_file"
    args = json.loads(calls[0]["function"]["arguments"])
    assert args["path"] == "index.html"
    assert args["content"].startswith("<!DOCTYPE html>") and "</html>" in args["content"]


def test_a_call_missing_a_required_argument_is_refused():
    """Half a call is worse than none — it looks like progress. Measured: 80 recovered `write`s
    carried a `path` but no `content` (the model wrote `< code>`, not `<parameter=content>`), each landed
    as an "empty code" tool error, and the model spent the build cap arguing about tool syntax."""
    content = "<tool_call>\n<function=write_file>\n<parameter=path>\nindex.html\n</parameter>\n</function>"
    assert parse_tool_calls(content, SCHEMAS) == []


def test_an_optional_argument_may_be_omitted():
    content = '<tool_call>\n{"name": "read_file", "arguments": {"path": "game.js"}}\n</tool_call>'
    calls = parse_tool_calls(content, SCHEMAS)
    assert len(calls) == 1 and calls[0]["function"]["name"] == "read_file"
