from llm_clients.message_builder import MessageBuilder


def test_build_is_system_then_every_message_untouched():
    # The window is compact's; the builder hands the transcript through whole.
    msgs = [
        {"role": "user", "content": "task"},
        {"role": "assistant", "content": "X" * 100_000},
        {"role": "user", "content": "now"},
    ]
    out = MessageBuilder("SYSTEM").extend(msgs).build()
    assert out == [{"role": "system", "content": "SYSTEM"}] + msgs
