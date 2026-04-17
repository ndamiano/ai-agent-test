"""Central message builder — single place where LLM message arrays are constructed."""

import json
import logging
from collections import defaultdict
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


class MessageBuilder:
    """
    Builds the messages list sent to the LLM.

    All callers — agents, pipeline runner, pipelines — must go through here
    so that cross-cutting concerns (truncation, token budgeting, logging) can
    be added in one place later without touching call sites.

    Usage (one-shot):
        messages = MessageBuilder(system_prompt).add_user(text).build()

    Usage (multi-turn, build once per API call):
        messages = MessageBuilder(system_prompt).extend(history).build()
    """

    TOOL_RESULT_MAX_CHARS: int = 8_000

    def __init__(self, system_prompt: str) -> None:
        from config.settings_manager import settings_manager
        self.MESSAGE_BUDGET_CHARS: int = settings_manager.get_category_settings().message_budget_chars
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

    def _deduplicate_tool_results(
        self, messages: List[Dict[str, Any]]
    ) -> List[Dict[str, Any]]:
        """
        For any tool called multiple times with identical (name, args), replace
        all but the most recent result with a short placeholder.  Keeps context
        lean when an agent re-reads the same file or re-runs the same search.
        """
        # Pass 1: map tool_call_id → (tool_name, normalised_args_str)
        call_info: Dict[str, Tuple[str, str]] = {}
        for msg in messages:
            if msg.get("role") != "assistant":
                continue
            for tc in msg.get("tool_calls") or []:
                if tc.get("type") != "function":
                    continue
                fn = tc.get("function", {})
                name = fn.get("name", "")
                raw_args = fn.get("arguments", "")
                try:
                    norm_args = json.dumps(json.loads(raw_args), sort_keys=True)
                except (json.JSONDecodeError, TypeError):
                    norm_args = raw_args
                call_info[tc.get("id", "")] = (name, norm_args)

        # Pass 2: group tool-result message indices by (name, args)
        groups: Dict[Tuple[str, str], List[int]] = defaultdict(list)
        for i, msg in enumerate(messages):
            if msg.get("role") != "tool":
                continue
            key = call_info.get(msg.get("tool_call_id", ""))
            if key:
                groups[key].append(i)

        # Pass 3: mark all but the last index in each group for omission (duplicate dedup)
        omit: Dict[int, str] = {}  # index → tool_name
        for (tool_name, _), indices in groups.items():
            if len(indices) > 1:
                for idx in indices[:-1]:
                    omit[idx] = tool_name

        # Pass 4: if any later result for same (tool, args) succeeded, also omit prior
        # failed results (even if they weren't exact duplicates of the successful one).
        def _is_failed(content: str) -> bool:
            return content.startswith("Tool '") and "failed:" in content or content.startswith("Error")

        for (tool_name, _), indices in groups.items():
            contents = [messages[i].get("content") or "" for i in indices]
            any_success = any(not _is_failed(c) for c in contents)
            if any_success:
                for idx, content in zip(indices, contents):
                    if _is_failed(content) and idx not in omit:
                        omit[idx] = tool_name

        if not omit:
            return messages

        # Pass 5: prune assistant messages whose tool_calls are fully or partially omitted.
        # Build set of omitted tool_call_ids for fast lookup.
        omitted_call_ids: set = set()
        for i in omit:
            omitted_call_ids.add(messages[i].get("tool_call_id", ""))

        result = []
        for i, msg in enumerate(messages):
            if i in omit:
                result.append({
                    **msg,
                    "content": f"[output omitted — superseded by later call to {omit[i]}]",
                })
            elif msg.get("role") == "assistant" and msg.get("tool_calls"):
                remaining = [
                    tc for tc in msg["tool_calls"]
                    if tc.get("id") not in omitted_call_ids
                ]
                if len(remaining) == len(msg["tool_calls"]):
                    # Nothing omitted — keep as-is.
                    result.append(msg)
                elif remaining:
                    # Some calls omitted — keep msg with pruned tool_calls.
                    result.append({**msg, "tool_calls": remaining})
                else:
                    # All calls omitted — keep msg shell only if it has content.
                    pruned = {k: v for k, v in msg.items() if k != "tool_calls"}
                    if pruned.get("content"):
                        result.append(pruned)
                    # else: drop the message entirely — empty shell, no content.
            else:
                result.append(msg)
        return result

    def _cap_tool_results(
        self, messages: List[Dict[str, Any]]
    ) -> List[Dict[str, Any]]:
        """
        Truncate any tool result content exceeding TOOL_RESULT_MAX_CHARS.
        Keeps the first and last half, with an omission notice in the middle.
        """
        result = []
        half = self.TOOL_RESULT_MAX_CHARS // 2
        for msg in messages:
            if msg.get("role") == "tool":
                content = msg.get("content") or ""
                if len(content) > self.TOOL_RESULT_MAX_CHARS:
                    omitted = len(content) - self.TOOL_RESULT_MAX_CHARS
                    content = (
                        content[:half]
                        + f"\n[... {omitted} characters truncated ...]\n"
                        + content[-half:]
                    )
                    msg = {**msg, "content": content}
            result.append(msg)
        return result

    def _enforce_budget(
        self, messages: List[Dict[str, Any]]
    ) -> List[Dict[str, Any]]:
        """
        Drop oldest messages until total char count fits within MESSAGE_BUDGET_CHARS.

        Never drops:
        - The most recent user message (the current turn).
        - Any message that would break tool_call_id linkage: a tool result is only
          dropped together with its paired assistant tool-call message, and vice-versa.

        Drops from the front of the list (oldest first).
        """

        def _msg_chars(msg: Dict[str, Any]) -> int:
            content = msg.get("content") or ""
            tool_calls_str = json.dumps(msg.get("tool_calls", [])) if msg.get("tool_calls") else ""
            return len(content) + len(tool_calls_str)

        total = sum(_msg_chars(m) for m in messages)
        if total <= self.MESSAGE_BUDGET_CHARS:
            return messages

        # Index of last user message — always protected.
        last_user_idx = max(
            (i for i, m in enumerate(messages) if m.get("role") == "user"),
            default=None,
        )

        dropped = 0
        result = list(messages)
        i = 0
        while i < len(result) and total > self.MESSAGE_BUDGET_CHARS:
            msg = result[i]

            # Never drop the last user message.
            if i == last_user_idx:
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
                "(was %d chars).",
                dropped,
                self.MESSAGE_BUDGET_CHARS,
                sum(_msg_chars(m) for m in messages),
            )

        return result

    # ── Output ─────────────────────────────────────────────────────────────

    def build(self) -> List[Dict[str, Any]]:
        """Return the complete messages list: [system] + accumulated messages."""
        messages = self._deduplicate_tool_results(self._messages)
        messages = self._cap_tool_results(messages)
        messages = self._enforce_budget(messages)
        return [{"role": "system", "content": self._system}] + messages
