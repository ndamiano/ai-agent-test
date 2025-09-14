"""Main conversational agent that orchestrates tools"""

from typing import List, Dict, Any, Optional
import json
from connectors.connector_selector import get_connector
from tools.tool_manager import tool_manager
from config.agent_prompts import SYSTEM_PROMPT

class MainAgent:
    """
    Main conversational agent that orchestrates AI connectors and tools.
    Handles message history and provides intelligent tool selection and usage.
    """
    
    def __init__(self, use_native_tools: bool = True):
        """Initialize the agent with connector and tool manager access
        
        Args:
            use_native_tools: Whether to use native tool calling or text-based fallback
        """
        self.connector = get_connector("main_agent", "conversation", "text")
        self.message_history: List[Dict[str, str]] = []
        self.use_native_tools = use_native_tools
        self.system_context = SYSTEM_PROMPT
        self._tools_schema = self._build_tools_schema() if use_native_tools else None

    def _build_tools_schema(self) -> List[Dict[str, Any]]:
        """Convert tool manager tools to OpenAI function calling format"""
        tools = tool_manager.getTools()
        openai_tools = []
        
        for tool in tools:
            # Convert parameter info to JSON schema
            properties = {}
            required = []
            
            for param_name, param_info in tool['parameters'].items():
                param_type = param_info['type']
                
                # Convert Python types to JSON schema types
                if param_type == str or param_type == 'str':
                    json_type = "string"
                elif param_type == int or param_type == 'int':
                    json_type = "integer"
                elif param_type == float or param_type == 'float':
                    json_type = "number"
                elif param_type == bool or param_type == 'bool':
                    json_type = "boolean"
                elif param_type == list or param_type == 'list':
                    json_type = "array"
                elif param_type == dict or param_type == 'dict':
                    json_type = "object"
                else:
                    json_type = "string"  # Default fallback
                
                properties[param_name] = {"type": json_type}
                
                if param_info['required']:
                    required.append(param_name)
            
            # Create OpenAI tool schema
            openai_tool = {
                "type": "function",
                "function": {
                    "name": tool['name'],
                    "description": tool['description'],
                    "parameters": {
                        "type": "object",
                        "properties": properties,
                        "required": required
                    }
                }
            }
            
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
        
        Args:
            message: User's message
            
        Returns:
            AI response, potentially including tool usage results
        """
        # Add user message to history
        self.message_history.append({"role": "user", "content": message})
        
        try:
            if self.use_native_tools and hasattr(self.connector, 'generate_with_tools'):
                return self._chat_with_native_tools(message)
            else:
                return self._chat_with_text_fallback(message)
                
        except Exception as e:
            error_response = f"Sorry, I encountered an error: {str(e)}"
            self.message_history.append({"role": "assistant", "content": error_response})
            return error_response

    def _chat_with_native_tools(self, message: str) -> str:
        """Handle chat using native tool calling capabilities"""
        # Prepare messages for native tool calling
        messages = [{"role": "system", "content": SYSTEM_PROMPT}]
        
        # Add conversation history
        messages.extend(self.message_history)
        
        # Get response with tools
        response = self.connector.generate_with_tools(messages, self._tools_schema)
        
        if "error" in response:
            # Fallback to text-based if native tools fail
            return self._chat_with_text_fallback(message)
        
        # Process the response
        choice = response.get("choices", [{}])[0]
        message_response = choice.get("message", {})
        content = message_response.get("content", "")
        tool_calls = message_response.get("tool_calls", [])
        
        # Execute any tool calls
        tool_results = []
        for tool_call in tool_calls:
            if tool_call.get("type") == "function":
                function = tool_call.get("function", {})
                tool_name = function.get("name")
                try:
                    arguments = json.loads(function.get("arguments", "{}"))
                    result = tool_manager.useTool(tool_name, **arguments)
                    tool_results.append(f"Tool '{tool_name}' executed successfully:\n{result}")
                except Exception as e:
                    tool_results.append(f"Tool '{tool_name}' failed: {str(e)}")
        
        # Combine content and tool results
        if tool_results:
            final_response = f"{content}\n\n" + "\n\n".join(tool_results)
        else:
            final_response = content
        
        # Add to history
        self.message_history.append({"role": "assistant", "content": final_response})
        return final_response

    def _chat_with_text_fallback(self, message: str) -> str:
        """Handle chat using text-based tool calling fallback"""
        # Build conversation context
        conversation_context = self._build_conversation_context()
        
        # Get AI response
        ai_response = self.connector.generate(message, conversation_context)
        
        # Check if response contains tool usage
        tool_result = self._process_potential_tool_usage(ai_response)
        
        if tool_result:
            # Tool was used, append result to response
            final_response = f"{ai_response}\n\n{tool_result}"
        else:
            final_response = ai_response
        
        # Add AI response to history
        self.message_history.append({"role": "assistant", "content": final_response})
        
        return final_response
    
    def _build_conversation_context(self) -> str:
        """Build context string from system context and message history"""
        context_parts = [self.system_context]
        
        # Add recent message history (last 10 messages to avoid context overflow)
        if self.message_history:
            context_parts.append("Recent conversation:")
            recent_messages = self.message_history[-10:]  # Last 10 messages
            for msg in recent_messages:
                context_parts.append(f"{msg['role'].title()}: {msg['content']}")
        
        return "\n\n".join(context_parts)
    
    def _process_potential_tool_usage(self, ai_response: str) -> Optional[str]:
        """
        Check if AI response contains tool usage and execute if found
        
        Args:
            ai_response: The AI's response text
            
        Returns:
            Tool execution result if tool was used, None otherwise
        """
        try:
            # Look for JSON tool usage pattern in the response
            if '{"tool_name"' in ai_response:
                # Find JSON in response
                start_idx = ai_response.find('{"tool_name"')
                if start_idx != -1:
                    # Find end of JSON (simple approach - look for closing brace)
                    brace_count = 0
                    end_idx = start_idx
                    for i, char in enumerate(ai_response[start_idx:], start_idx):
                        if char == '{':
                            brace_count += 1
                        elif char == '}':
                            brace_count -= 1
                            if brace_count == 0:
                                end_idx = i + 1
                                break
                    
                    json_str = ai_response[start_idx:end_idx]
                    tool_request = json.loads(json_str)
                    
                    if "tool_name" in tool_request and "arguments" in tool_request:
                        tool_name = tool_request["tool_name"]
                        arguments = tool_request["arguments"]
                        
                        # Execute the tool
                        result = tool_manager.useTool(tool_name, **arguments)
                        return f"Tool '{tool_name}' executed successfully:\n{result}"
            
            return None
            
        except (json.JSONDecodeError, KeyError, ValueError) as e:
            return f"Error processing tool request: {str(e)}"
        except Exception as e:
            return f"Tool execution failed: {str(e)}"