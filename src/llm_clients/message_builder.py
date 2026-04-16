"""Central message builder — single place where LLM message arrays are constructed."""

from typing import Any, Dict, List, Optional


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

    def __init__(self, system_prompt: str) -> None:
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

    # ── Output ─────────────────────────────────────────────────────────────

    def build(self) -> List[Dict[str, Any]]:
        """Return the complete messages list: [system] + accumulated messages."""
        return [{"role": "system", "content": self._system}] + self._messages
