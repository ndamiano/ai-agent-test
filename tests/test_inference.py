from llm_clients.inference import safe_history_content


def test_safe_history_content_omits_channel_tagged_thoughts():
    content = "<|channel>thought\nnot valid request content"
    assert safe_history_content(content) == "[previous response omitted: invalid provider channel markup]"


def test_safe_history_content_keeps_final_channel_content():
    content = "<|channel>thought\nscratch\n<|channel>final\n{\"ok\": true}"
    assert safe_history_content(content) == '{"ok": true}'
