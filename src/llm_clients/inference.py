"""Shared inference primitives for pipeline stages."""

import logging
from typing import Any, Dict, List

from llm_clients.message_builder import MessageBuilder
from llm_clients.connector_selector import get_connector

logger = logging.getLogger(__name__)


def strip_fences(content: str) -> str:
    """Strip markdown code fences that models occasionally emit despite instructions."""
    if content.startswith("```"):
        content = content.split("```")[1]
        if content.startswith("json"):
            content = content[4:]
    return content.strip()


def call_llm(connector, messages: list, json_mode: bool = False) -> dict:
    """Call connector with streaming if available, fall back to non-streaming."""
    if not hasattr(connector, "generate_with_tools_stream"):
        return connector.generate_with_tools(messages, [], json_mode=json_mode)

    accumulated = ""
    envelope: dict = {}
    try:
        for chunk in connector.generate_with_tools_stream(messages, []):
            if "error" in chunk:
                logger.warning(f"Streaming failed, falling back: {chunk['error']}")
                return connector.generate_with_tools(messages, [], json_mode=json_mode)
            if not envelope:
                envelope = {k: v for k, v in chunk.items() if k != "choices"}
            for choice in chunk.get("choices", []):
                delta = choice.get("delta", {})
                if delta.get("content"):
                    accumulated += delta["content"]
    except Exception as e:
        logger.warning(f"Streaming error, falling back: {e}")
        return connector.generate_with_tools(messages, [], json_mode=json_mode)

    if not accumulated:
        logger.warning("Streaming produced empty content, falling back to non-streaming")
        return connector.generate_with_tools(messages, [], json_mode=json_mode)

    return {
        **envelope,
        "choices": [{"message": {"role": "assistant", "content": accumulated}}],
    }


class PipelineAgent:
    """
    Stateful inference agent for pipeline stages.
    No tools. Maintains message history so stages can do multi-turn correction.

    Usage:
        agent = PipelineAgent()
        content = agent.send("generate character as JSON")
        # on bad output:
        content = agent.send("Invalid JSON. Return only the JSON object.")
    """

    DEFAULT_SYSTEM = (
        "You are a precise assistant. Output only valid JSON. "
        "No markdown, no explanation, no code fences."
    )

    def __init__(self, system_prompt: str = DEFAULT_SYSTEM):
        self._system = system_prompt
        self._history: List[Dict[str, Any]] = []

    def send(self, message: str) -> str:
        from config.settings_manager import settings_manager
        json_mode = settings_manager.get_category_settings().use_json_mode

        self._history.append(MessageBuilder.user_msg(message))
        messages = MessageBuilder(self._system).extend(self._history).build()

        result = call_llm(get_connector(), messages, json_mode=json_mode)

        if "error" in result:
            raise RuntimeError(f"LLM error: {result['error']}")

        content = result["choices"][0]["message"]["content"].strip()
        self._history.append(MessageBuilder.assistant_msg(content))
        return content
