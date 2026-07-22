import json
import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)
from api.websocket.event_bus import event_bus
from config.settings_manager import settings_manager
from config.time_utils import get_utc_timestamp
from llm_clients.connector import get_connector
from llm_clients.message_builder import MessageBuilder
from llm_clients.openai_compatible_connector import _log_response_to_file
from tools.execution_context import (
    execution_context,
    get_subtask_id,
    get_task_id,
    resolve_base_path,
    user_id_scope,
)
from tools.tool_manager import build_openai_tool_schema, tool_manager

from .agent_store import get_agent


class MainAgent:

    def __init__(
        self,
        agent_id: str,
        system_prompt: Optional[str] = None,
        user_id: Optional[str] = None,
    ):
        self.connector = get_connector()
        self.user_id = user_id
        self.message_history: List[Dict[str, str]] = []

        agent_data = get_agent(agent_id)
        self.system_context = agent_data["system_prompt"]
        self._tools_schema = self._build_tools_schema(agent_data["tools"])

        if system_prompt is not None:
            self.system_context = system_prompt

    def _build_tools_schema(self, allowed_tools: Optional[List[str]] = None) -> List[Dict[str, Any]]:
        tools = tool_manager.getTools()
        openai_tools = []

        for tool in tools:
            if allowed_tools is None or tool['name'] in allowed_tools:
                openai_tool = build_openai_tool_schema(tool)
                openai_tools.append(openai_tool)

        return openai_tools

    def _stream_llm_response(self, messages: List[Dict[str, str]], tools: List[Dict[str, Any]]):
        """Generator yielding ('token', str) as assistant content streams in, then a final
        ('response', dict) in the same shape `generate_with_tools` returns (so callers that
        only want the finished message can drain this without caring how it arrived).
        """
        if not (hasattr(self.connector, 'generate_with_tools_stream')
                and getattr(self.connector, '_streaming_works', True)):
            yield ('response', self.connector.generate_with_tools(messages, tools))
            return

        try:
            # Envelope fields from the first chunk (id, model, etc.)
            envelope: Dict[str, Any] = {}
            # One accumulated message per choice index.
            acc_messages: Dict[int, Dict[str, Any]] = {}
            # Finish reasons per choice index.
            finish_reasons: Dict[int, Any] = {}

            for chunk in self.connector.generate_with_tools_stream(messages, tools):
                if "error" in chunk:
                    logging.warning(f"Streaming failed, falling back to non-streaming: {chunk['error']}")
                    yield ('response', self.connector.generate_with_tools(messages, tools))
                    return

                # Capture envelope fields once.
                if not envelope:
                    envelope = {k: v for k, v in chunk.items() if k != "choices"}

                for choice in chunk.get("choices", []):
                    i = choice.get("index", 0)
                    if i not in acc_messages:
                        acc_messages[i] = {"role": "assistant", "content": "", "tool_calls": []}

                    acc_msg = acc_messages[i]
                    delta = choice.get("delta", {})

                    if delta.get("content"):
                        acc_msg["content"] += delta["content"]
                        if i == 0:
                            yield ('token', delta["content"])

                    for tc_delta in delta.get("tool_calls", []):
                        idx = tc_delta.get("index", 0)
                        while len(acc_msg["tool_calls"]) <= idx:
                            acc_msg["tool_calls"].append({
                                "id": "",
                                "type": "function",
                                "function": {"name": "", "arguments": ""},
                            })
                        acc_tc = acc_msg["tool_calls"][idx]
                        if tc_delta.get("id"):
                            acc_tc["id"] = tc_delta["id"]
                        if tc_delta.get("type"):
                            acc_tc["type"] = tc_delta["type"]
                        fn_delta = tc_delta.get("function", {})
                        if fn_delta.get("name"):
                            acc_tc["function"]["name"] += fn_delta["name"]
                        if fn_delta.get("arguments"):
                            acc_tc["function"]["arguments"] += fn_delta["arguments"]

                    if choice.get("finish_reason"):
                        finish_reasons[i] = choice["finish_reason"]
        except Exception as e:
            logging.warning(f"Streaming failed, falling back to non-streaming: {e}")
            yield ('response', self.connector.generate_with_tools(messages, tools))
            return

        if not acc_messages:
            yield ('response', {"error": "Empty streaming response"})
            return

        # Reconstruct a non-streaming response shape.
        choices = []
        for i, msg in sorted(acc_messages.items()):
            if not msg["tool_calls"]:
                del msg["tool_calls"]
            choices.append({
                "index": i,
                "message": msg,
                "finish_reason": finish_reasons.get(i),
                "logprobs": None,
            })

        assembled = {**envelope, "choices": choices}

        _log_response_to_file(
            assembled,
            getattr(self.connector, 'api_endpoint', 'unknown'),
            {"method": "generate_with_tools_stream", "model": getattr(self.connector, 'model_name', 'unknown')},
        )

        yield ('response', assembled)

    def _get_response_with_tools(self, messages: List[Dict[str, str]], tools: List[Dict[str, Any]]) -> Dict:
        response: Optional[Dict[str, Any]] = None
        for kind, payload in self._stream_llm_response(messages, tools):
            if kind == 'response':
                response = payload
        return response if response is not None else {"error": "Empty streaming response"}

    def _sanitize_tool_arguments(self, tool_name: str, args: Dict) -> Dict:
        sensitive_keys = ['password', 'token', 'secret', 'key', 'credential', 'api_key']
        sanitized = {}

        for k, v in args.items():
            if any(sens in k.lower() for sens in sensitive_keys):
                sanitized[k] = "[REDACTED]"
            elif isinstance(v, str) and len(v) > 100:
                sanitized[k] = v[:100] + "..."
            else:
                sanitized[k] = v

        return sanitized

    def _broadcast_tool_usage(self, tool_name: str, arguments: Dict, status: str):
        task_id = get_task_id()
        if task_id:
            sanitized = self._sanitize_tool_arguments(tool_name, arguments)
            event_bus.publish_sync({
                'type': 'tool_usage',
                'task_id': task_id,
                'subtask_id': get_subtask_id(),
                'tool_name': tool_name,
                'arguments': sanitized,
                'status': status,
                'timestamp': get_utc_timestamp()
            })

    def chat(self, message: str) -> str:
        """Non-streaming convenience wrapper: drains `chat_stream` and returns the final text."""
        final = ""
        for event in self.chat_stream(message):
            if event["type"] in ("done", "error"):
                final = event.get("message", "")
        return final

    def chat_stream(self, message: str):
        """Generator yielding streaming chat events as the agentic tool-calling turn runs:

        - {"type": "token", "content": str}                         assistant text as it streams
        - {"type": "tool", "tool_name": str, "status": "start"}      a tool call is about to run
        - {"type": "tool", "tool_name": str, "status": "success"|"failed"}  it finished
        - {"type": "done", "message": str}                           the turn's final reply
        - {"type": "error", "message": str}                          the turn failed

        Exactly one of "done"/"error" terminates the stream. `self.message_history` is
        updated the same way `chat()` used to, so callers can freely mix streaming and
        non-streaming turns on one session.
        """
        self.message_history.append(MessageBuilder.user_msg(message))

        try:
            yield from self._agentic_loop_stream()
        except Exception as e:
            error_response = f"Sorry, I encountered an error: {str(e)}"
            self.message_history.append(MessageBuilder.assistant_msg(error_response))
            yield {"type": "error", "message": error_response}

    def _agentic_loop_stream(self):
        max_iterations = settings_manager.get_category_settings().max_iterations
        iteration = 0
        content = ""

        while iteration < max_iterations:
            system_content = self.system_context
            path = resolve_base_path()
            if path:
                system_content = f"{system_content}\n\nWorking Directory: {path}\nAll file operations use paths relative to this working directory unless you use absolute paths."

            messages = MessageBuilder(system_content).extend(self.message_history).build()

            response = None
            streamed_content = ""
            for kind, payload in self._stream_llm_response(messages, self._tools_schema):
                if kind == 'token':
                    streamed_content += payload
                    yield {"type": "token", "content": payload}
                else:
                    response = payload

            if response is None or "error" in response:
                err = response.get("error") if response else "Empty response"
                yield {"type": "error", "message": f"Connector error: {err}"}
                return

            choice = response.get("choices", [{}])[0]
            message_response = choice.get("message", {})
            content = message_response.get("content") or ""
            tool_calls = message_response.get("tool_calls", [])

            # Fallback path (streaming unavailable/failed): no 'token' events fired above,
            # so surface the whole reply as one chunk rather than a silent block.
            if not streamed_content and content:
                yield {"type": "token", "content": content}

            # Strip malformed tool calls (empty name) before storing — LM Studio 500s
            # if these are sent back in subsequent requests.
            valid_tool_calls = [
                tc for tc in tool_calls
                if tc.get("type") == "function" and tc.get("function", {}).get("name")
            ]
            if len(valid_tool_calls) < len(tool_calls):
                logger.warning(
                    "Dropped %d malformed tool call(s) with empty name.",
                    len(tool_calls) - len(valid_tool_calls),
                )

            # Normalize empty arguments string — LM Studio 500s if arguments is ""
            for tc in valid_tool_calls:
                fn = tc.get("function", {})
                if not fn.get("arguments"):
                    fn["arguments"] = "{}"

            self.message_history.append(
                MessageBuilder.assistant_msg(content, valid_tool_calls or None)
            )

            if not valid_tool_calls:
                yield {"type": "done", "message": content}
                return

            ctx_task_id = get_task_id()
            ctx_subtask_id = get_subtask_id()

            with execution_context(task_id=ctx_task_id, subtask_id=ctx_subtask_id, working_directory=path):
                for tool_call in valid_tool_calls:
                    function = tool_call.get("function", {})
                    tool_name = function.get("name")
                    tool_call_id = tool_call.get("id")

                    yield {"type": "tool", "tool_name": tool_name, "status": "start"}

                    arguments = {}
                    try:
                        raw_args = function.get("arguments") or "{}"
                        # Some local models wrap args in markdown fences or emit bare "".
                        raw_args = raw_args.strip()
                        if raw_args.startswith("```"):
                            raw_args = raw_args.split("```")[1]
                            if raw_args.startswith("json"):
                                raw_args = raw_args[4:]
                            raw_args = raw_args.strip()
                        if not raw_args:
                            raw_args = "{}"
                        arguments = json.loads(raw_args)

                        with user_id_scope(self.user_id):
                            result = tool_manager.useTool(tool_name, **arguments)

                        self._broadcast_tool_usage(tool_name, arguments, 'success')

                        self.message_history.append(
                            MessageBuilder.tool_msg(tool_call_id, str(result))
                        )

                        yield {"type": "tool", "tool_name": tool_name, "status": "success"}

                    except Exception as e:

                        self._broadcast_tool_usage(tool_name, arguments, 'failed')

                        self.message_history.append(
                            MessageBuilder.tool_msg(tool_call_id, f"Tool '{tool_name}' failed: {str(e)}")
                        )

                        yield {"type": "tool", "tool_name": tool_name, "status": "failed"}

            iteration += 1

        yield {"type": "done", "message": content}