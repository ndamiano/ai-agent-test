"""Central message builder — single place where LLM message arrays are constructed."""

import json
import logging
from collections import defaultdict
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# Tools whose result carries the full current file body — deduped by file, so the newest body of a
# file supersedes every earlier read/edit body of it.
_FILE_BODY_TOOLS = {"read_file", "edit"}


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

    TOOL_RESULT_MAX_CHARS: int = 32_000

    def __init__(self, system_prompt: str) -> None:
        from config.settings_manager import settings_manager
        budget = settings_manager.get_category_settings().message_budget_chars
        try:
            from llm_clients.connector_selector import get_connector
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
                    parsed = json.loads(raw_args)
                    norm_args = json.dumps(parsed, sort_keys=True)
                except (json.JSONDecodeError, TypeError):
                    parsed, norm_args = None, raw_args
                # A FULL read_file and every edit return the CURRENT file body; key them by
                # file (not name+args) so a fresh read/edit of a file supersedes every earlier body
                # of it — old_string/new_string differ per edit, so name+args would never collapse.
                # A PARTIAL read (offset/limit) is only a slice — key it normally so it neither
                # supersedes nor is superseded by the full body.
                is_partial_read = (name == "read_file" and isinstance(parsed, dict)
                                   and (parsed.get("offset") is not None or parsed.get("limit") is not None))
                if name in _FILE_BODY_TOOLS and not is_partial_read \
                        and isinstance(parsed, dict) and parsed.get("file"):
                    call_info[tc.get("id", "")] = ("__filebody__", parsed["file"])
                else:
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
        messages = self._deduplicate_tool_results(self._messages)
        messages = self._cap_tool_results(messages)
        messages = self._enforce_budget(messages)
        return [{"role": "system", "content": self._system}] + messages
