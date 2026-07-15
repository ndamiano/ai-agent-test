"""Contract tests for MessageBuilder's three stateful context transforms.

These transforms (`_deduplicate_tool_results`, `_cap_tool_results`,
`_enforce_budget`) shape every LLM message array in the app and had NO tests —
they rot silently because a regression still produces a *valid-looking* message
list, just one that leaks context, blows the budget, or breaks tool linkage.
Each test feeds a real message list and asserts the transformed output.
"""

import pytest

from llm_clients.message_builder import MessageBuilder


@pytest.fixture
def mb(monkeypatch):
    """A MessageBuilder with deterministic budget knobs.

    The constructor probes the live connector for a context window; force the
    except-path so no test depends on a network endpoint. Tests then set the
    two knobs (`MESSAGE_BUDGET_CHARS`, `TOOL_RESULT_MAX_CHARS`) they exercise.
    """
    import llm_clients.message_builder as mod
    monkeypatch.setattr(
        mod, "get_connector", None, raising=False
    )
    # get_connector is imported inside __init__; patch the source module too.
    import llm_clients.connector_selector as cs
    monkeypatch.setattr(cs, "get_connector", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("no net")))
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

def test_dedup_collapses_duplicate_call_keeping_latest(mb):
    # WHY: an agent re-reading the same file must not carry both copies — the
    # OLDER result is replaced by a placeholder and the MOST RECENT is kept
    # verbatim. If this flips, stale output wins and context bloats.
    msgs = [
        _assistant_call("c1", "read", '{"f": "a"}', content="reading"),
        _tool_result("c1", "OLD CONTENTS"),
        _assistant_call("c2", "read", '{"f": "a"}', content="reading again"),
        _tool_result("c2", "NEW CONTENTS"),
    ]
    out = mb._deduplicate_tool_results(msgs)

    tool_msgs = [m for m in out if m.get("role") == "tool"]
    assert len(tool_msgs) == 2
    old, new = tool_msgs
    assert "OLD CONTENTS" not in old["content"]
    assert "omitted" in old["content"]
    assert new["content"] == "NEW CONTENTS"


def test_dedup_edit_supersedes_earlier_read_of_same_file(mb):
    # WHY: read_game_file and edit_game_file both return the file's full body. They are keyed by
    # FILE, not name+args (old_string/new_string differ per edit) — so a later edit's body must
    # supersede the earlier read body of the same file, leaving only the newest copy in context.
    msgs = [
        _assistant_call("c1", "read_game_file", '{"file": "types.ts"}', content="r"),
        _tool_result("c1", "OLD BODY"),
        _assistant_call("c2", "edit_game_file",
                        '{"file": "types.ts", "old_string": "a", "new_string": "b"}', content="e"),
        _tool_result("c2", "NEW BODY"),
    ]
    out = mb._deduplicate_tool_results(msgs)
    tool_msgs = [m for m in out if m.get("role") == "tool"]
    assert "omitted" in tool_msgs[0]["content"]
    assert tool_msgs[1]["content"] == "NEW BODY"


def test_dedup_file_body_keeps_distinct_files(mb):
    # WHY: file-keying must not collapse ACROSS files — different files' bodies both survive.
    msgs = [
        _assistant_call("c1", "read_game_file", '{"file": "a.ts"}', content="r"),
        _tool_result("c1", "A BODY"),
        _assistant_call("c2", "edit_game_file",
                        '{"file": "b.ts", "old_string": "x", "new_string": "y"}', content="e"),
        _tool_result("c2", "B BODY"),
    ]
    out = mb._deduplicate_tool_results(msgs)
    contents = [m["content"] for m in out if m.get("role") == "tool"]
    assert contents == ["A BODY", "B BODY"]


def test_dedup_normalizes_arg_order_when_matching(mb):
    # WHY: identical args in a different key order are the SAME call — dedup
    # normalizes JSON before comparing, so reordering must still collapse.
    msgs = [
        _assistant_call("c1", "search", '{"q": "x", "n": 5}', content="s"),
        _tool_result("c1", "FIRST"),
        _assistant_call("c2", "search", '{"n": 5, "q": "x"}', content="s"),
        _tool_result("c2", "SECOND"),
    ]
    out = mb._deduplicate_tool_results(msgs)
    tool_msgs = [m for m in out if m.get("role") == "tool"]
    assert "omitted" in tool_msgs[0]["content"]
    assert tool_msgs[1]["content"] == "SECOND"


def test_dedup_leaves_distinct_calls_untouched(mb):
    # WHY: different args are different calls — dedup must NOT collapse them, or
    # the agent loses real results. Returns the list unchanged when nothing dupes.
    msgs = [
        _assistant_call("c1", "read", '{"f": "a"}', content="s"),
        _tool_result("c1", "AAA"),
        _assistant_call("c2", "read", '{"f": "b"}', content="s"),
        _tool_result("c2", "BBB"),
    ]
    out = mb._deduplicate_tool_results(msgs)
    contents = [m["content"] for m in out if m.get("role") == "tool"]
    assert contents == ["AAA", "BBB"]


def test_dedup_omits_failed_result_superseded_by_success(mb):
    # WHY: a failed tool call retried to success should not leave the error text
    # in context — the failed earlier result is dropped once a later success for
    # the same (name,args) exists.
    msgs = [
        _assistant_call("c1", "read", '{"f": "a"}', content="try"),
        _tool_result("c1", "Error: file locked"),
        _assistant_call("c2", "read", '{"f": "a"}', content="retry"),
        _tool_result("c2", "actual file body"),
    ]
    out = mb._deduplicate_tool_results(msgs)
    tool_msgs = [m for m in out if m.get("role") == "tool"]
    assert "Error: file locked" not in tool_msgs[0]["content"]
    assert "omitted" in tool_msgs[0]["content"]
    assert tool_msgs[1]["content"] == "actual file body"


# ── _cap_tool_results ────────────────────────────────────────────────────────

@pytest.mark.parametrize("length,expect_truncated", [
    (100, False),   # exactly at cap — only strictly-greater is cut
    (99, False),    # under cap — untouched
    (400, True),    # well over cap — head+tail kept, middle elided
])
def test_cap_tool_result_boundary(mb, length, expect_truncated):
    # WHY: the cap is a `> MAX` char rule on tool-result content only. An
    # off-by-one (>= vs >) or a byte/char mixup would silently maim results at
    # the boundary. Head and tail must survive; the middle is what's dropped.
    # (Note: content only a hair over the cap gets LONGER, since the elision
    # notice outweighs the ~1 char saved — truncation only shrinks well above
    # the notice size, so this pins a length where the drop is real.)
    mb.TOOL_RESULT_MAX_CHARS = 100
    body = "H" * (length // 2) + "T" * (length - length // 2)
    msgs = [_tool_result("c1", body)]
    out = mb._cap_tool_results(msgs)
    content = out[0]["content"]
    if expect_truncated:
        assert "truncated" in content
        assert len(content) < length
        assert content.startswith("H")
        assert content.endswith("T")
    else:
        assert content == body


def test_cap_ignores_non_tool_messages(mb):
    # WHY: the cap targets tool results specifically — a long user/assistant turn
    # must pass through untouched, or user intent gets silently mangled.
    mb.TOOL_RESULT_MAX_CHARS = 10
    long_user = "u" * 500
    msgs = [{"role": "user", "content": long_user},
            {"role": "assistant", "content": "a" * 500}]
    out = mb._cap_tool_results(msgs)
    assert out[0]["content"] == long_user
    assert out[1]["content"] == "a" * 500


# ── _enforce_budget ──────────────────────────────────────────────────────────

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
        _assistant_call("c1", "read_game_file", '{"file": "a.ts"}'),
        _tool_result("c1", "A" * 200),                                     # oldest read — droppable
        _assistant_call("c2", "read_game_file", '{"file": "b.ts"}'),
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
    # overflows. When the connector reports n_ctx (via /v1/models or the lmstudio.n_ctx fallback), the
    # budget is 50% of the window in chars (~4 chars/token) — half for input, half reserved for output.
    import llm_clients.connector_selector as cs

    class _FakeConn:
        def get_context_length(self):
            return 32768

    monkeypatch.setattr(cs, "get_connector", lambda *a, **k: _FakeConn())
    b = MessageBuilder("SYS")
    assert b.MESSAGE_BUDGET_CHARS == 32768 * 4 // 2 == 65536


# ── build(): the composed pipeline ───────────────────────────────────────────

def test_build_prepends_system_and_applies_transforms(mb):
    # WHY: build() is the only public entry — it must prepend the system prompt
    # AND run all three transforms. This pins that dedup + cap both fire through
    # the real composition, not just in isolation.
    mb.TOOL_RESULT_MAX_CHARS = 50
    mb.MESSAGE_BUDGET_CHARS = 10_000
    mb.extend([
        _assistant_call("c1", "read", '{"f": "a"}', content="r"),
        _tool_result("c1", "OLD"),
        _assistant_call("c2", "read", '{"f": "a"}', content="r"),
        _tool_result("c2", "N" * 200),  # duplicate key AND over cap
    ])
    out = mb.build()

    assert out[0] == {"role": "system", "content": "SYSTEM"}
    tool_msgs = [m for m in out if m.get("role") == "tool"]
    # older duplicate omitted
    assert any("omitted" in m["content"] for m in tool_msgs)
    # surviving latest result was capped
    assert any("truncated" in m["content"] for m in tool_msgs)
