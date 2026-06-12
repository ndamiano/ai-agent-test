"""Shared inference primitives for pipeline stages."""

import json
import logging
import re
from typing import Any, Dict, List, Optional

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


JSON_SYSTEM = (
    "You are a precise creative writing assistant. Output only valid JSON. "
    "No markdown, no explanation, no code fences."
)


_REPETITION_RE = re.compile(r'(.{3,20})\1{8,}')


def _is_repetitive(content: str) -> bool:
    return bool(_REPETITION_RE.search(content))


def make_response_format(name: str, schema: dict) -> dict:
    return {
        "type": "json_schema",
        "json_schema": {
            "name": name,
            "strict": "true",
            "schema": schema,
        },
    }


def safe_history_content(content: str) -> str:
    """
    Keep provider/internal channel tags out of subsequent LLM requests.

    Some local endpoints reject messages containing raw strings such as
    <|channel>thought. Pipeline stages still receive the original content so
    they can attempt JSON parsing/correction, but history stores a harmless
    placeholder instead of echoing malformed channel markup back to the API.
    """
    if "<|channel>" in content or "<|start_header_id|>" in content:
        if "<|channel>final" in content:
            return content.split("<|channel>final", 1)[1].strip()
        return "[previous response omitted: invalid provider channel markup]"
    return content


def call_llm(connector, messages: list, response_format: Optional[dict] = None, max_tokens: Optional[int] = None) -> dict:
    """Call connector with streaming if available, fall back to non-streaming."""
    if (response_format
            or not hasattr(connector, "generate_with_tools_stream")
            or not getattr(connector, "_streaming_works", True)):
        return connector.generate_with_tools(messages, [], response_format=response_format, max_tokens=max_tokens)

    accumulated = ""
    envelope: dict = {}
    try:
        for chunk in connector.generate_with_tools_stream(messages, []):
            if "error" in chunk:
                logger.warning(f"Streaming failed, falling back: {chunk['error']}")
                return connector.generate_with_tools(messages, [], response_format=response_format, max_tokens=max_tokens)
            if not envelope:
                envelope = {k: v for k, v in chunk.items() if k != "choices"}
            for choice in chunk.get("choices", []):
                delta = choice.get("delta") or choice.get("message", {})
                if delta.get("content"):
                    accumulated += delta["content"]
    except Exception as e:
        logger.warning(f"Streaming error, falling back: {e}")
        return connector.generate_with_tools(messages, [], response_format=response_format, max_tokens=max_tokens)

    if not accumulated:
        logger.warning("Streaming produced empty content, falling back to non-streaming")
        connector._streaming_works = False
        return connector.generate_with_tools(messages, [], response_format=response_format, max_tokens=max_tokens)

    return {
        **envelope,
        "choices": [{"message": {"role": "assistant", "content": accumulated}}],
    }


def json_with_correction(agent: "PipelineAgent", prompt: str, label: str, attempts: int = 2) -> dict:
    """Send a prompt expecting JSON; on parse failure, ask the agent to correct itself."""
    content = strip_fences(agent.send(prompt))
    for _ in range(attempts):
        try:
            return json.loads(content)
        except json.JSONDecodeError:
            content = strip_fences(agent.send(
                "Invalid JSON. Return only the JSON object, no other text."
            ))
    raise RuntimeError(f"Failed to get valid JSON for {label}")


class PipelineAgent:
    """
    Stateful inference agent for pipeline stages.
    No tools. Maintains message history so stages can do multi-turn correction.

    Usage:
        agent = PipelineAgent()
        content = agent.send("generate character as JSON", response_format=MY_SCHEMA)
        # on bad output:
        content = agent.send("Invalid JSON. Return only the JSON object.", response_format=MY_SCHEMA)
    """

    DEFAULT_SYSTEM = (
        "You are a precise assistant. Output only valid JSON. "
        "No markdown, no explanation, no code fences."
    )

    def __init__(self, system_prompt: str = DEFAULT_SYSTEM, max_tokens: int = 2500, history: Optional[List[Dict[str, Any]]] = None):
        self._system = system_prompt
        self._max_tokens = max_tokens
        self._history: List[Dict[str, Any]] = list(history) if history else []

    def send(self, message: str, response_format: Optional[dict] = None) -> str:
        self._history.append(MessageBuilder.user_msg(message))
        messages = MessageBuilder(self._system).extend(self._history).build()

        result = call_llm(get_connector(), messages, response_format=response_format, max_tokens=self._max_tokens)

        if "error" in result:
            raise RuntimeError(f"LLM error: {result['error']}")

        content = result["choices"][0]["message"]["content"].strip()
        if _is_repetitive(content):
            raise RuntimeError("Model output detected as repetitive — retrying with fresh context")
        if content:
            self._history.append(MessageBuilder.assistant_msg(safe_history_content(content)))
        return content
