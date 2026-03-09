"""Main conversational agent that orchestrates tools with a proper agentic loop"""

from typing import List, Dict, Any, Optional
import json
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
    
    def __init__(self, agent_id: Optional[str] = None):
        """Initialize the agent with connector and tool manager access
        
        Args:
            agent_id: Optional agent ID to load from agent store. If provided,
                     uses the agent's system prompt and tool list instead of defaults.
        """
        self.connector = get_connector("main_agent", "conversation", "text")
        self.message_history: List[Dict[str, str]] = []
        
        # Load agent from store if agent_id provided
        if agent_id:
            agent_store = AgentStore()
            try:
                agent_data = agent_store.get(agent_id)
                self.system_context = agent_data["system_prompt"]
                self._tools_schema = self._build_tools_schema(agent_data["tools"])
            except KeyError:
                # Fall back to default behavior if agent not found
                self.system_context = SYSTEM_PROMPT
                self._tools_schema = self._build_tools_schema()
        else:
            self.system_context = SYSTEM_PROMPT
            self._tools_schema = self._build_tools_schema()

    def _build_tools_schema(self, allowed_tools: Optional[List[str]] = None) -> List[Dict[str, Any]]:
        """Convert tool manager tools to OpenAI function calling format
        
        Args:
            allowed_tools: Optional list of tool names to include. If None, includes all tools.
        """
        from tools.tool_manager import build_openai_tool_schema
        
        tools = tool_manager.getTools()
        openai_tools = []
        
        for tool in tools:
            # If allowed_tools is specified, only include tools in the list
            if allowed_tools is None or tool['name'] in allowed_tools:
                openai_tool = build_openai_tool_schema(tool)
                openai_tools.append(openai_tool)
        
        return openai_tools

    def get_message_history(self) -> List[Dict[str, str]]:
        """Get the current message history"""
        return self.message_history.copy()
    
    def clear_history(self):
        """Clear the message history"""
        self.message_history.clear()
    
    def chat(self, message: str) -> str:
        """
        Process a chat message and return response, potentially using tools
        
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
        # Add user message to history
        self.message_history.append({"role": "user", "content": message})
        
        try:
            return self._agentic_loop_with_native_tools(message)
        except Exception as e:
            error_response = f"Sorry, I encountered an error: {str(e)}"
            self.message_history.append({"role": "assistant", "content": error_response})
            return error_response

    def _agentic_loop_with_native_tools(self, user_message: str) -> str:
        """Handle chat using native tool calling with proper agentic loop
        
        Args:
            user_message: The original user message for logging purposes
        """
        max_iterations = 10  # Prevent infinite loops
        iteration = 0
        content = ""  # Initialize content variable
        
        while iteration < max_iterations:
            # Prepare messages for native tool calling
            messages = [{"role": "system", "content": self.system_context}]
            messages.extend(self.message_history)
            
            # Get response with tools
            response = self.connector.generate_with_tools(messages, self._tools_schema)
            
            if "error" in response:
                raise Exception(f"Connector error: {response['error']}")
            
            # Process the response
            choice = response.get("choices", [{}])[0]
            message_response = choice.get("message", {})
            content = message_response.get("content", "")
            tool_calls = message_response.get("tool_calls", [])
            
            # Add assistant message (with tool_calls) to history
            assistant_message = {"role": "assistant", "content": content}
            if tool_calls:
                assistant_message["tool_calls"] = tool_calls
            self.message_history.append(assistant_message)
            
            # If no tool calls, we're done
            if not tool_calls:
                return content
            
            # Execute tool calls and add results to history
            for tool_call in tool_calls:
                if tool_call.get("type") == "function":
                    function = tool_call.get("function", {})
                    tool_name = function.get("name")
                    tool_call_id = tool_call.get("id")
                    
                    arguments = {}
                    try:
                        arguments = json.loads(function.get("arguments", "{}"))
                        
                        # Log the tool call
                        log_agent_decision(
                            user_input=user_message,
                            decision=f"Executing tool: {tool_name}",
                            details={"tool_name": tool_name, "arguments": arguments}
                        )
                        
                        result = tool_manager.useTool(tool_name, **arguments)
                        
                        # Log successful tool execution
                        log_tool_call(tool_name, arguments, result)
                        
                        # Add tool result message to history
                        tool_result_message = {
                            "role": "tool",
                            "tool_call_id": tool_call_id,
                            "content": str(result)
                        }
                        self.message_history.append(tool_result_message)
                        
                    except Exception as e:
                        # Log failed tool execution
                        log_tool_call(tool_name, arguments, None, str(e))
                        
                        # Add error result message to history
                        tool_result_message = {
                            "role": "tool",
                            "tool_call_id": tool_call_id,
                            "content": f"Tool '{tool_name}' failed: {str(e)}"
                        }
                        self.message_history.append(tool_result_message)
            
            iteration += 1
        
        # If we hit max iterations, return the last response
        return content

