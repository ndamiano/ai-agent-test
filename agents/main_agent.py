"""Main conversational agent that orchestrates tools with a proper agentic loop"""

from typing import List, Dict, Any, Optional, Callable
import json
from datetime import datetime
from connectors.connector_selector import get_connector
from tools.tool_manager import tool_manager
from config.agent_prompts import SYSTEM_PROMPT
from tools.logging_utils import log_tool_call, log_agent_decision, log_error
from .agent_store import AgentStore


class MainAgent:
    """
    Main conversational agent that orchestrates AI connectors and tools.
    Implements a proper agentic loop: send messages → receive response → execute tool calls → repeat.
    """

    def __init__(
        self,
        agent_id: Optional[str] = None,
        max_history_length: int = 20,
        system_prompt: Optional[str] = None,
    ):
        """
        Initialize the agent.

        Args:
            agent_id:         Load system prompt and tool list from the agent store.
            max_history_length: Max messages to keep in history.
            system_prompt:    Override the system prompt after loading from the store.
                              Useful for injecting rendered template variables (e.g.
                              {{AGENT_ROSTER}}) before the agent makes its first call.
                              If agent_id is also provided, this replaces the stored prompt.
        """
        self.connector = get_connector("main_agent", "conversation", "text")
        self.message_history: List[Dict[str, str]] = []
        self.max_history_length = max_history_length

        # Broadcasting context for tool usage events
        self.broadcast_fn: Optional[Callable] = None
        self.broadcast_context: Dict[str, str] = {}

        if agent_id:
            agent_store = AgentStore()
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

        # Apply override last so it always wins
        if system_prompt is not None:
            self.system_context = system_prompt

    def _build_tools_schema(self, allowed_tools: Optional[List[str]] = None) -> List[Dict[str, Any]]:
        """Convert tool manager tools to OpenAI function calling format

        Args:
            allowed_tools: Optional list of tool names to include. If None, includes all tools.
        """
        from tools.tool_manager import build_openai_tool_schema

        tools = tool_manager.getTools()
        openai_tools = []

        for tool in tools:
            if allowed_tools is None or tool['name'] in allowed_tools:
                openai_tool = build_openai_tool_schema(tool)
                openai_tools.append(openai_tool)

        return openai_tools

    def set_broadcast_context(self, task_id: str, subtask_id: str, broadcast_fn: Callable):
        """Set context for broadcasting tool usage events

        Args:
            task_id: ID of the current task
            subtask_id: ID of the current subtask
            broadcast_fn: Function to call for broadcasting events
        """
        self.broadcast_fn = broadcast_fn
        self.broadcast_context = {
            'task_id': task_id,
            'subtask_id': subtask_id
        }

    def _sanitize_tool_arguments(self, tool_name: str, args: Dict) -> Dict:
        """Remove sensitive data from tool arguments for broadcasting

        Args:
            tool_name: Name of the tool
            args: Tool arguments to sanitize

        Returns:
            Sanitized arguments dictionary
        """
        # Sensitive patterns to redact
        sensitive_keys = ['password', 'token', 'secret', 'key', 'credential', 'api_key']
        sanitized = {}

        for k, v in args.items():
            # Redact sensitive keys
            if any(sens in k.lower() for sens in sensitive_keys):
                sanitized[k] = "[REDACTED]"
            # Truncate long string values
            elif isinstance(v, str) and len(v) > 100:
                sanitized[k] = v[:100] + "..."
            else:
                sanitized[k] = v

        return sanitized

    def _broadcast_tool_usage(self, tool_name: str, arguments: Dict, status: str):
        """Broadcast tool usage event if broadcast_fn is set

        Args:
            tool_name: Name of the tool used
            arguments: Tool arguments
            status: 'success' or 'failed'
        """
        if self.broadcast_fn and self.broadcast_context:
            sanitized = self._sanitize_tool_arguments(tool_name, arguments)
            self.broadcast_fn({
                'type': 'tool_usage',
                'task_id': self.broadcast_context['task_id'],
                'subtask_id': self.broadcast_context['subtask_id'],
                'tool_name': tool_name,
                'arguments': sanitized,
                'status': status,
                'timestamp': datetime.now().isoformat()
            })

    def get_message_history(self) -> List[Dict[str, str]]:
        """Get the current message history"""
        return self.message_history.copy()

    def clear_history(self):
        """Clear the message history"""
        self.message_history.clear()

    def _trim_history(self):
        """Trim message history to stay within length limits"""
        if len(self.message_history) > self.max_history_length:
            self.message_history = self.message_history[-self.max_history_length:]

    def chat(self, message: str) -> str:
        """
        Process a chat message and return response, potentially using tools.

        Implements proper agentic loop:
        1. Send messages to LLM
        2. Receive response
        3. If tool calls exist, execute them and feed results back
        4. Repeat until no tool calls

        Args:
            message: User's message

        Returns:
            AI response, potentially including tool usage results
        """
        self.message_history.append({"role": "user", "content": message})

        try:
            return self._agentic_loop_with_native_tools(message)
        except Exception as e:
            error_response = f"Sorry, I encountered an error: {str(e)}"
            self.message_history.append({"role": "assistant", "content": error_response})
            return error_response

    def _agentic_loop_with_native_tools(self, user_message: str) -> str:
        """Handle chat using native tool calling with proper agentic loop"""
        max_iterations = 10
        iteration = 0
        content = ""

        while iteration < max_iterations:
            messages = [{"role": "system", "content": self.system_context}]
            messages.extend(self.message_history)

            response = self.connector.generate_with_tools(messages, self._tools_schema)

            if "error" in response:
                raise Exception(f"Connector error: {response['error']}")

            choice = response.get("choices", [{}])[0]
            message_response = choice.get("message", {})
            content = message_response.get("content", "")
            tool_calls = message_response.get("tool_calls", [])

            assistant_message = {"role": "assistant", "content": content}
            if tool_calls:
                assistant_message["tool_calls"] = tool_calls
            self.message_history.append(assistant_message)

            if not tool_calls:
                return content

            for tool_call in tool_calls:
                if tool_call.get("type") == "function":
                    function = tool_call.get("function", {})
                    tool_name = function.get("name")
                    tool_call_id = tool_call.get("id")

                    arguments = {}
                    try:
                        arguments = json.loads(function.get("arguments", "{}"))

                        log_agent_decision(
                            user_input=user_message,
                            decision=f"Executing tool: {tool_name}",
                            details={"tool_name": tool_name, "arguments": arguments}
                        )

                        result = tool_manager.useTool(tool_name, **arguments)
                        log_tool_call(tool_name, arguments, result)

                        # Broadcast successful tool usage
                        self._broadcast_tool_usage(tool_name, arguments, 'success')

                        self.message_history.append({
                            "role": "tool",
                            "tool_call_id": tool_call_id,
                            "content": str(result)
                        })

                    except Exception as e:
                        log_tool_call(tool_name, arguments, None, str(e))

                        # Broadcast failed tool usage
                        self._broadcast_tool_usage(tool_name, arguments, 'failed')

                        self.message_history.append({
                            "role": "tool",
                            "tool_call_id": tool_call_id,
                            "content": f"Tool '{tool_name}' failed: {str(e)}"
                        })

            iteration += 1

        return content