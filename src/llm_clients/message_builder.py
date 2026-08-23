"""Central message builder — the single seam every LLM message array passes through.

Message CONTENT is never edited here, and nothing is trimmed: the window is owned by
`build_steps.compact`, which drops whole rounds and re-grounds the model.
"""

from typing import Any, Dict, List


class MessageBuilder:
    """
    Builds the messages list sent to the LLM.

    Usage (one-shot):
        messages = MessageBuilder(system_prompt).add_user(text).build()

    Usage (multi-turn, build once per API call):
        messages = MessageBuilder(system_prompt).extend(history).build()
    """


    def __init__(self, system_prompt: str) -> None:
        self._system = system_prompt
        self._messages: List[Dict[str, Any]] = []

    def add_user(self, content: str) -> "MessageBuilder":
        self._messages.append({"role": "user", "content": content})
        return self

    def extend(self, messages: List[Dict[str, Any]]) -> "MessageBuilder":
        self._messages.extend(messages)
        return self


    def build(self) -> List[Dict[str, Any]]:
        return [{"role": "system", "content": self._system}] + self._messages
