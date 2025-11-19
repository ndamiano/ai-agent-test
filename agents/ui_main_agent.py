"""Enhanced MainAgent with UI callback support for real-time tool execution updates"""

from typing import List, Dict, Any, Optional, Callable
import json
from agents.main_agent import MainAgent
from tools.tool_manager import tool_manager


class UIMainAgent(MainAgent):
    """
    Enhanced MainAgent that supports UI callbacks for real-time tool execution updates.
    Extends the base MainAgent to provide hooks for UI integration.
    """
    
    def __init__(self, use_native_tools: bool = True, 
                 tool_callback: Optional[Callable[[str, Dict[str, Any], str], None]] = None):
        """
        Initialize the UI-enabled agent
        
        Args:
            use_native_tools: Whether to use native tool calling or text-based fallback
            tool_callback: Callback function for tool execution updates
                          Signature: (tool_name: str, arguments: Dict, status: str) -> None
        """
        super().__init__(use_native_tools)
        self.tool_callback = tool_callback
    
    def set_tool_callback(self, callback: Callable[[str, Dict[str, Any], str], None]):
        """Set the tool execution callback"""
        self.tool_callback = callback
    
    def _chat_with_native_tools(self, message: str) -> str:
        """Enhanced version with UI callbacks for native tool calling"""
        # Prepare messages for native tool calling
        from config.agent_prompts import SYSTEM_PROMPT
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
        
        # Execute any tool calls with UI updates
        tool_results = []
        for tool_call in tool_calls:
            if tool_call.get("type") == "function":
                function = tool_call.get("function", {})
                tool_name = function.get("name")
                
                try:
                    arguments = json.loads(function.get("arguments", "{}"))
                    
                    # Notify UI that tool execution is starting
                    if self.tool_callback:
                        self.tool_callback(tool_name, arguments, "started")
                    
                    # Execute the tool
                    result = tool_manager.useTool(tool_name, **arguments)
                    
                    # Notify UI that tool execution completed
                    if self.tool_callback:
                        self.tool_callback(tool_name, arguments, "completed")
                    
                    tool_results.append(f"Tool '{tool_name}' executed successfully:\n{result}")
                    
                except Exception as e:
                    # Notify UI that tool execution failed
                    if self.tool_callback:
                        self.tool_callback(tool_name, arguments if 'arguments' in locals() else {}, "error")
                    
                    tool_results.append(f"Tool '{tool_name}' failed: {str(e)}")
        
        # Combine content and tool results
        if tool_results:
            final_response = f"{content}\n\n" + "\n\n".join(tool_results)
        else:
            final_response = content
        
        # Add to history
        self.message_history.append({"role": "assistant", "content": final_response})
        return final_response
    
    def _process_potential_tool_usage(self, ai_response: str) -> Optional[str]:
        """Enhanced version with UI callbacks for text-based tool calling"""
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
                        
                        try:
                            # Notify UI that tool execution is starting
                            if self.tool_callback:
                                self.tool_callback(tool_name, arguments, "started")
                            
                            # Execute the tool
                            result = tool_manager.useTool(tool_name, **arguments)
                            
                            # Notify UI that tool execution completed
                            if self.tool_callback:
                                self.tool_callback(tool_name, arguments, "completed")
                            
                            return f"Tool '{tool_name}' executed successfully:\n{result}"
                            
                        except Exception as e:
                            # Notify UI that tool execution failed
                            if self.tool_callback:
                                self.tool_callback(tool_name, arguments, "error")
                            
                            return f"Tool '{tool_name}' failed: {str(e)}"
            
            return None
            
        except (json.JSONDecodeError, KeyError, ValueError) as e:
            return f"Error processing tool request: {str(e)}"
        except Exception as e:
            return f"Tool execution failed: {str(e)}"
    
    def get_available_tools_info(self) -> str:
        """Get formatted information about available tools for display"""
        tools = tool_manager.getTools()
        
        if not tools:
            return "No tools available"
        
        info_lines = ["Available Tools:"]
        for tool in tools:
            info_lines.append(f"\n• {tool['name']}")
            info_lines.append(f"  Description: {tool['description']}")
            
            if tool['parameters']:
                param_info = []
                for param_name, param_info_dict in tool['parameters'].items():
                    required_marker = " (required)" if param_info_dict['required'] else " (optional)"
                    param_info.append(f"{param_name}{required_marker}")
                info_lines.append(f"  Parameters: {', '.join(param_info)}")
            else:
                info_lines.append("  Parameters: None")
        
        return "\n".join(info_lines)
    
    def get_conversation_summary(self) -> Dict[str, Any]:
        """Get a summary of the current conversation state"""
        return {
            "message_count": len(self.message_history),
            "use_native_tools": self.use_native_tools,
            "has_tool_callback": self.tool_callback is not None,
            "recent_messages": self.message_history[-5:] if self.message_history else []
        }