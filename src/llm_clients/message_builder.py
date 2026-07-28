"""Central message builder — the single seam every LLM message array passes through.

Message CONTENT is never edited here. The only transform is the budget backstop, which drops whole
messages and never breaks tool_call linkage.
"""

import json
import logging
from typing import Any, Dict, List, Optional

from config.settings_manager import settings_manager
from llm_clients.connector import get_connector

logger = logging.getLogger(__name__)


class MessageBuilder:
    """
    Builds the messages list sent to the LLM.

    All callers must go through here
    so that cross-cutting concerns (truncation, token budgeting, logging) can
    be added in one place later without touching call sites.

    Usage (one-shot):
        messages = MessageBuilder(system_prompt).add_user(text).build()

    Usage (multi-turn, build once per API call):
        messages = MessageBuilder(system_prompt).extend(history).build()
    """


    def __init__(self, system_prompt: str) -> None:
        budget = settings_manager.get_category_settings().message_budget_chars
        try:
            ctx_len = get_connector().get_context_length()
            if ctx_len:
                # Input budget = (window − reserved output) × chars/token. Measured on live
                # code-heavy payloads (120 jobs, 2026-07-21): 3.5–3.9 chars/token, median 3.67 —
                # NOT the 4:1 prose heuristic. Output reservation matches the largest completion
                # the build path requests (16K, module._CODE_MAX_TOKENS); without it the biggest
                # fix calls could only truncate their OUTPUT, which is how a long tool-call write
                # comes back cut off mid-arg.
                # Floor at a third of the window: a small-context model can't reserve 16K of
                # output, but must still get a usable input slice.
                budget = int(max(ctx_len - 16_000, ctx_len // 3) * 3.5)
        except Exception:
            pass
        self.MESSAGE_BUDGET_CHARS: int = budget
        self._system = system_prompt
        self._messages: List[Dict[str, Any]] = []

    # ── Append helpers ─────────────────────────────────────────────────────

    def add_user(self, content: str) -> "MessageBuilder":
        self._messages.append({"role": "user", "content": content})
        return self

    def add_assistant(
        self,
        content: str,
        tool_calls: Optional[List[Dict]] = None,
    ) -> "MessageBuilder":
        msg: Dict[str, Any] = {"role": "assistant", "content": content}
        if tool_calls:
            msg["tool_calls"] = tool_calls
        self._messages.append(msg)
        return self

    def add_tool_result(self, tool_call_id: str, content: str) -> "MessageBuilder":
        self._messages.append({
            "role": "tool",
            "tool_call_id": tool_call_id,
            "content": content,
        })
        return self

    def extend(self, messages: List[Dict[str, Any]]) -> "MessageBuilder":
        """Append a pre-built list of messages (e.g. an existing history)."""
        self._messages.extend(messages)
        return self

    # ── Static factories (for appending single messages to stored history) ──

    @staticmethod
    def user_msg(content: str) -> Dict[str, Any]:
        return {"role": "user", "content": content}

    @staticmethod
    def assistant_msg(
        content: str,
        tool_calls: Optional[List[Dict]] = None,
    ) -> Dict[str, Any]:
        msg: Dict[str, Any] = {"role": "assistant", "content": content}
        if tool_calls:
            msg["tool_calls"] = tool_calls
        return msg

    @staticmethod
    def tool_msg(tool_call_id: str, content: str) -> Dict[str, Any]:
        return {"role": "tool", "tool_call_id": tool_call_id, "content": content}

    # ── Context optimisation ───────────────────────────────────────────────

    def _enforce_budget(
        self, messages: List[Dict[str, Any]]
    ) -> List[Dict[str, Any]]:
        """
        Drop oldest messages until total char count fits within MESSAGE_BUDGET_CHARS.

        Never drops:
        - The FIRST user message (the task/instructions anchor) and the LAST user message
          (the current turn). In an agent loop the task lives in the first user turn; dropping it
          strands the fix with no gate/authority to act on.
        - Any message that would break tool_call_id linkage: a tool result is only
          dropped together with its paired assistant tool-call message, and vice-versa.

        Drops from the front of the list (oldest first) — so the oldest tool reads go first, keeping
        the task + the most RECENT (most relevant) file bodies.
        """

        def _msg_chars(msg: Dict[str, Any]) -> int:
            content = msg.get("content") or ""
            tool_calls_str = json.dumps(msg.get("tool_calls", [])) if msg.get("tool_calls") else ""
            return len(content) + len(tool_calls_str)

        # MESSAGE_BUDGET_CHARS is the INPUT half of the window (system + messages). The system prompt
        # is prepended AFTER this pass but shares that half, so charge it against the budget here —
        # otherwise a fat system prompt (e.g. the 9-16KB kit doc) silently pushes the real prompt over
        # the window. Floor at 0 so a system larger than the budget still trims messages to nothing.
        budget = max(self.MESSAGE_BUDGET_CHARS - len(self._system or ""), 0)

        total = sum(_msg_chars(m) for m in messages)
        if total <= budget:
            return messages

        # First + last user messages — always protected (task anchor + current turn). Tracked by object
        # identity, not index, since `result` is mutated as messages are dropped.
        user_msgs = [m for m in messages if m.get("role") == "user"]
        protected = {id(user_msgs[0]), id(user_msgs[-1])} if user_msgs else set()

        dropped = 0
        result = list(messages)
        i = 0
        while i < len(result) and total > budget:
            msg = result[i]

            # Never drop a protected (first/last) user message.
            if id(msg) in protected:
                i += 1
                continue

            # Drop assistant messages that carry tool_calls together with their
            # paired tool results (to preserve tool_call_id linkage).
            if msg.get("role") == "assistant" and msg.get("tool_calls"):
                call_ids = {tc.get("id") for tc in msg["tool_calls"]}
                # Find all paired tool-result indices immediately following.
                paired = [
                    j for j in range(i + 1, len(result))
                    if result[j].get("role") == "tool"
                    and result[j].get("tool_call_id") in call_ids
                ]
                # Drop assistant msg + all paired results together.
                indices_to_drop = sorted({i} | set(paired), reverse=True)
                for idx in indices_to_drop:
                    total -= _msg_chars(result[idx])
                    result.pop(idx)
                    dropped += 1
                # Don't advance i — next msg is now at the same index.
                continue

            # Drop any other message (user turns, plain assistant turns, orphaned tool results).
            total -= _msg_chars(msg)
            result.pop(i)
            dropped += 1

        if dropped:
            logger.warning(
                "MessageBuilder: dropped %d message(s) to stay within %d-char budget "
                "(%d for system, was %d chars).",
                dropped,
                budget,
                len(self._system or ""),
                sum(_msg_chars(m) for m in messages),
            )

        return result

    # ── Output ─────────────────────────────────────────────────────────────

    def build(self) -> List[Dict[str, Any]]:
        """Return the complete messages list: [system] + accumulated messages."""
        return [{"role": "system", "content": self._system}] + self._enforce_budget(self._messages)
