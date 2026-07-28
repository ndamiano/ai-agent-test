"""Contract tests for MessageBuilder's three stateful context transforms.

The remaining transform (`_enforce_budget`,
`_enforce_budget`) shape every LLM message array in the app and had NO tests —
they rot silently because a regression still produces a *valid-looking* message
list, just one that leaks context, blows the budget, or breaks tool linkage.
Each test feeds a real message list and asserts the transformed output.
"""

import pytest

import llm_clients.message_builder as mod
from llm_clients.message_builder import MessageBuilder


@pytest.fixture
def mb(monkeypatch):
    """A MessageBuilder with deterministic budget knobs.

    The constructor probes the live connector for a context window; force the
    except-path so no test depends on a network endpoint. Tests then set the
    two knobs (`MESSAGE_BUDGET_CHARS`, `TOOL_RESULT_MAX_CHARS`) they exercise.
    """
    monkeypatch.setattr(mod, "get_connector",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("no net")))
    return MessageBuilder("SYSTEM")


def _assistant_call(call_id, name, args_json, content=""):
    return {
        "role": "assistant",
        "content": content,
        "tool_calls": [
            {"id": call_id, "type": "function",
             "function": {"name": name, "arguments": args_json}}
        ],
    }


def _tool_result(call_id, content):
    return {"role": "tool", "tool_call_id": call_id, "content": content}


# ── _deduplicate_tool_results ────────────────────────────────────────────────

def test_budget_under_limit_unchanged(mb):
    # WHY: when the conversation fits, the transform must be a no-op — dropping
    # anything early would throw away history for no reason.
    mb.MESSAGE_BUDGET_CHARS = 10_000
    msgs = [
        {"role": "user", "content": "hi"},
        {"role": "assistant", "content": "hello"},
        {"role": "user", "content": "again"},
    ]
    out = mb._enforce_budget(msgs)
    assert out == msgs


def test_budget_drops_oldest_first_protects_latest_user(mb):
    # WHY: over budget, the OLDEST turns are dropped and the current user turn
    # (the actual question being answered) must survive — trimming the latest
    # turn would answer the wrong prompt.
    mb.MESSAGE_BUDGET_CHARS = 60
    msgs = [
        {"role": "user", "content": "task"},          # first user — protected (task anchor)
        {"role": "assistant", "content": "Y" * 100},  # old, droppable
        {"role": "user", "content": "now"},           # latest user — protected
    ]
    out = mb._enforce_budget(msgs)
    assert {"role": "user", "content": "now"} in out
    # the fat old middle turn is gone
    assert all(m["content"] != "Y" * 100 for m in out)


def test_budget_protects_first_user_task_anchor(mb):
    # WHY: an agent loop puts the TASK (gate + authority) in the first user turn, then appends reads.
    # A late nudge makes a newer user turn, but the task must never be trimmed away — dropping it
    # strands the fix with nothing to act on. First AND last user turns survive; oldest reads go first.
    mb.MESSAGE_BUDGET_CHARS = 80
    msgs = [
        {"role": "user", "content": "TASK: fix the gate"},                 # first user — protected
        _assistant_call("c1", "read_file", '{"file": "a.ts"}'),
        _tool_result("c1", "A" * 200),                                     # oldest read — droppable
        _assistant_call("c2", "read_file", '{"file": "b.ts"}'),
        _tool_result("c2", "B" * 200),                                     # newer read — droppable
        {"role": "user", "content": "call a tool"},                        # nudge (last user) — protected
    ]
    out = mb._enforce_budget(msgs)
    assert {"role": "user", "content": "TASK: fix the gate"} in out        # task survived
    assert {"role": "user", "content": "call a tool"} in out              # current turn survived
    assert all("A" * 200 != (m.get("content") or "") for m in out)        # oldest read trimmed


def test_budget_drops_toolcall_and_result_together(mb):
    # WHY: dropping an assistant tool_call without its paired tool result (or
    # vice-versa) breaks tool_call_id linkage and the API rejects the request.
    # They must leave together, never orphaning a tool result.
    mb.MESSAGE_BUDGET_CHARS = 30
    msgs = [
        _assistant_call("c1", "read", '{"f": "a"}', content="Z" * 200),
        _tool_result("c1", "big tool output " * 20),
        {"role": "user", "content": "now"},  # protected latest user
    ]
    out = mb._enforce_budget(msgs)
    # no tool result may remain without its assistant call
    call_ids = {
        tc["id"]
        for m in out if m.get("role") == "assistant"
        for tc in m.get("tool_calls", [])
    }
    orphans = [m for m in out if m.get("role") == "tool"
               and m["tool_call_id"] not in call_ids]
    assert orphans == []
    assert {"role": "user", "content": "now"} in out


def test_budget_derives_from_context_window(monkeypatch):
    # WHY: the budget MUST track the server's real context window, else it never trims and the prompt
    # overflows. When the connector reports n_ctx, the budget is (window - 16K reserved output
    # tokens) x 3.5 chars/token — the ratio MEASURED on live code-heavy payloads (median 3.67),
    # floored at a third of the window for small-context models.
    class _FakeConn:
        def get_context_length(self):
            return 32768

    monkeypatch.setattr(mod, "get_connector", lambda *a, **k: _FakeConn())
    b = MessageBuilder("SYS")
    assert b.MESSAGE_BUDGET_CHARS == int(max(32768 - 16_000, 32768 // 3) * 3.5) == 58688


def test_budget_charges_system_prompt_against_input_half(monkeypatch):
    # WHY: MESSAGE_BUDGET_CHARS is the INPUT half of the window (system + messages share it). A fat
    # system prompt (e.g. the fix loop's 9-16KB kit doc) must eat into the budget, else the real prompt
    # silently overflows the window. Same messages fit with a small system, get trimmed with a big one.
    monkeypatch.setattr(mod, "get_connector",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("no net")))
    msgs = [
        {"role": "user", "content": "task"},
        {"role": "assistant", "content": "X" * 400},
        {"role": "user", "content": "now"},
    ]
    small = MessageBuilder("s")
    small.MESSAGE_BUDGET_CHARS = 500
    assert small._enforce_budget(list(msgs)) == msgs        # 407 chars fit under 500 - 1

    big = MessageBuilder("S" * 200)
    big.MESSAGE_BUDGET_CHARS = 500
    out = big._enforce_budget(list(msgs))                   # effective budget 300 → the fat turn drops
    assert all(m["content"] != "X" * 400 for m in out)
    assert {"role": "user", "content": "now"} in out


# ── build(): the composed pipeline ───────────────────────────────────────────
