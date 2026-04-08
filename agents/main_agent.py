from typing import List, Dict, Any, Optional
import json
from llm_clients.connector_selector import get_connector
from tools.tool_manager import tool_manager
from tools.execution_context import execution_context
from config.agent_prompts import SYSTEM_PROMPT
from config.time_utils import get_utc_timestamp
from api.websocket.event_bus import event_bus
from .agent_store import agent_store


class MainAgent:
    """
    Main conversational agent that orchestrates AI connectors and tools.
    Implements a finite state machine
    Planning -> Execution -> Validation -> Compiling -> Finished
    """

    def __init__(
        self,
        agent_id: Optional[str] = None,
        max_history_length: int = 20,
        system_prompt: Optional[str] = None,
    ):
        self.connector = get_connector()
        self.message_history: List[Dict[str, str]] = []
        self.max_history_length = max_history_length

        if agent_id:
            try:
                agent_data = agent_store.get(agent_id)
                self.system_context = agent_data["system_prompt"]
                self._tools_schema = self._build_tools_schema(agent_data["tools"])
            except KeyError:
                self.system_context = SYSTEM_PROMPT
                self._tools_schema = self._build_tools_schema()
        else:
            self.system_context = SYSTEM_PROMPT
            self._tools_schema = self._build_tools_schema()

        if system_prompt is not None:
            self.system_context = system_prompt

    def _build_tools_schema(self, allowed_tools: Optional[List[str]] = None) -> List[Dict[str, Any]]:
        from tools.tool_manager import build_openai_tool_schema

        tools = tool_manager.getTools()
        openai_tools = []

        for tool in tools:
            if allowed_tools is None or tool['name'] in allowed_tools:
                openai_tool = build_openai_tool_schema(tool)
                openai_tools.append(openai_tool)

        return openai_tools

    def _get_response_with_tools(self, messages: List[Dict[str, str]], tools: List[Dict[str, Any]]) -> Dict:
        if hasattr(self.connector, 'generate_with_tools_stream'):
            try:
                accumulated_response = None

                for chunk in self.connector.generate_with_tools_stream(messages, tools):
                    if "error" in chunk:
                        import logging
                        logging.warning(f"Streaming failed, falling back to non-streaming: {chunk['error']}")
                        return self.connector.generate_with_tools(messages, tools)

                    if accumulated_response is None:
                        accumulated_response = chunk
                    else:
                        if "choices" in chunk:
                            for i, choice in enumerate(chunk["choices"]):
                                if "delta" in choice:
                                    delta = choice["delta"]
                                    acc_choice = accumulated_response["choices"][i]
                                    acc_message = acc_choice.get("message", {})

                                    if "content" in delta and delta["content"]:
                                        content = acc_message.get("content", "")
                                        acc_message["content"] = content + delta["content"]

                                    if "tool_calls" in delta:
                                        if "tool_calls" not in acc_message:
                                            acc_message["tool_calls"] = []

                                        for tool_call_delta in delta["tool_calls"]:
                                            idx = tool_call_delta.get("index", 0)

                                            while len(acc_message["tool_calls"]) <= idx:
                                                acc_message["tool_calls"].append({
                                                    "id": "",
                                                    "type": "function",
                                                    "function": {"name": "", "arguments": ""}
                                                })

                                            acc_tool_call = acc_message["tool_calls"][idx]

                                            if "id" in tool_call_delta:
                                                acc_tool_call["id"] = tool_call_delta["id"]

                                            if "type" in tool_call_delta:
                                                acc_tool_call["type"] = tool_call_delta["type"]

                                            if "function" in tool_call_delta:
                                                func_delta = tool_call_delta["function"]
                                                acc_func = acc_tool_call["function"]

                                                if "name" in func_delta:
                                                    acc_func["name"] += func_delta["name"]

                                                if "arguments" in func_delta:
                                                    acc_func["arguments"] += func_delta["arguments"]

                                    acc_choice["message"] = acc_message

                return accumulated_response if accumulated_response else {"error": "Empty streaming response"}

            except Exception as e:
                import logging
                logging.warning(f"Streaming failed, falling back to non-streaming: {e}")
                return self.connector.generate_with_tools(messages, tools)
        else:
            return self.connector.generate_with_tools(messages, tools)

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
        # Get task_id and subtask_id from execution context
        from tools.execution_context import get_task_id, get_subtask_id
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

    def get_message_history(self) -> List[Dict[str, str]]:
        return self.message_history.copy()

    def clear_history(self):
        self.message_history.clear()

    def _trim_history(self):
        if len(self.message_history) > self.max_history_length:
            self.message_history = self.message_history[-self.max_history_length:]

    def chat(self, message: str) -> str:
        self.message_history.append({"role": "user", "content": message})

        try:
            return self._agentic_loop_with_native_tools(message)
        except Exception as e:
            error_response = f"Sorry, I encountered an error: {str(e)}"
            self.message_history.append({"role": "assistant", "content": error_response})
            return error_response

    def _agentic_loop_with_native_tools(self, user_message: str) -> str:
        max_iterations = 10
        iteration = 0
        content = ""

        while iteration < max_iterations:
            system_content = self.system_context
            from tools.execution_context import resolve_base_path
            path = resolve_base_path()
            if path:
                system_content = f"{system_content}\n\nWorking Directory: {path}\nAll file operations use paths relative to this working directory unless you use absolute paths."

            messages = [{"role": "system", "content": system_content}]
            messages.extend(self.message_history)

            response = self._get_response_with_tools(messages, self._tools_schema)

            if "error" in response:
                raise Exception(f"Connector error: {response['error']}")

            choice = response.get("choices", [{}])[0]
            message_response = choice.get("message", {})
            content = message_response.get("content") or ""
            tool_calls = message_response.get("tool_calls", [])

            assistant_message = {"role": "assistant", "content": content}
            if tool_calls:
                assistant_message["tool_calls"] = tool_calls
            self.message_history.append(assistant_message)

            if not tool_calls:
                return content

            # Get existing context if any
            from tools.execution_context import get_task_id, get_subtask_id
            ctx_task_id = get_task_id()
            ctx_subtask_id = get_subtask_id()

            with execution_context(task_id=ctx_task_id, subtask_id=ctx_subtask_id, working_directory=path):
                for tool_call in tool_calls:
                    if tool_call.get("type") == "function":
                        function = tool_call.get("function", {})
                        tool_name = function.get("name")
                        tool_call_id = tool_call.get("id")

                        arguments = {}
                        try:
                            arguments = json.loads(function.get("arguments", "{}"))

                            result = tool_manager.useTool(tool_name, **arguments)

                            self._broadcast_tool_usage(tool_name, arguments, 'success')

                            self.message_history.append({
                                "role": "tool",
                                "tool_call_id": tool_call_id,
                                "content": str(result)
                            })

                        except Exception as e:

                            self._broadcast_tool_usage(tool_name, arguments, 'failed')

                            self.message_history.append({
                                "role": "tool",
                                "tool_call_id": tool_call_id,
                                "content": f"Tool '{tool_name}' failed: {str(e)}"
                            })

            iteration += 1

        return content