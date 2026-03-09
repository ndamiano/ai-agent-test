"""Generic tool manager for registering and executing tools in an agentic loop"""

from typing import Dict, List, Any, Callable, Optional, Tuple, Union
import inspect
from threading import Lock

from .logging_utils import log_tool_call, log_error


class ToolManager:
    """
    Manages available tools in a clean, generic registry.
    Tools are registered explicitly at runtime, not auto-discovered.
    """
    
    _instance = None
    _lock = Lock()
    
    def __new__(cls):
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
        return cls._instance
    
    def __init__(self):
        if not hasattr(self, '_initialized'):
            self._tools_registry: Dict[str, Dict[str, Any]] = {}
            self._lock = Lock()
            self._initialized = True
    
    def register_tool(self, name: str, description: str, parameters: Dict[str, Any], fn: Callable) -> None:
        """
        Register a tool explicitly with the manager.
        
        Args:
            name: Unique tool name
            description: Tool description
            parameters: Parameter schema in OpenAI function calling format
            fn: Function to execute when tool is called
        """
        self._tools_registry[name] = {
            'name': name,
            'description': description,
            'parameters': parameters,
            'function': fn
        }
    
    def getTools(self) -> List[Dict[str, Any]]:
        """Get list of all available tools"""
        return list(self._tools_registry.values())
    
    def get_tool_info(self, tool_name: str) -> Optional[Dict[str, Any]]:
        """Get detailed information about a specific tool"""
        return self._tools_registry.get(tool_name)
    
    def useTool(self, tool_name: str, **kwargs) -> Any:
        """Execute a specific tool with provided arguments"""
        tool_info = self._tools_registry.get(tool_name)
        if not tool_info:
            error_msg = f"Tool '{tool_name}' not found"
            log_error("ToolNotFound", error_msg, {"requested_tool": tool_name})
            raise ValueError(error_msg)
        
        # Get the function to call
        fn = tool_info['function']
        
        # Validate and filter arguments
        validated_kwargs = self._validate_arguments(tool_info, kwargs)
        
        # Execute the tool
        try:
            result = fn(**validated_kwargs)
            return result
        except Exception as e:
            error_msg = f"Tool execution failed for '{tool_name}': {str(e)}"
            log_tool_call(tool_name, validated_kwargs, None, error_msg)
            raise RuntimeError(error_msg)
    
    def _validate_arguments(self, tool_info: Dict[str, Any], provided_kwargs: Dict[str, Any]) -> Dict[str, Any]:
        """Validate and filter provided arguments against tool parameters"""
        parameters = tool_info['parameters'].get('properties', {})
        required_params = tool_info['parameters'].get('required', [])
        validated = {}
        
        # Check required parameters
        for param_name in required_params:
            if param_name not in provided_kwargs:
                raise ValueError(f"Missing required parameter '{param_name}' for tool '{tool_info['name']}'")
        
        # Filter and validate provided parameters
        for param_name, value in provided_kwargs.items():
            if param_name in parameters:
                validated[param_name] = value
            # Silently ignore extra parameters to be flexible
        
        return validated
    
    def list_tools(self) -> str:
        """Get a formatted string listing available tools"""
        tools = self.getTools()
        
        if not tools:
            return "No tools available"
        
        lines = ["Available tools:"]
        for tool in tools:
            params = []
            properties = tool['parameters'].get('properties', {})
            required = tool['parameters'].get('required', [])
            
            for param_name in properties:
                required_marker = " (required)" if param_name in required else " (optional)"
                params.append(f"{param_name}{required_marker}")
            
            param_str = ", ".join(params) if params else "None"
            lines.append(f"  {tool['name']}({param_str}) - {tool['description']}")
        
        return "\n".join(lines)


def build_openai_tool_schema(tool: dict) -> dict:
    """
    Convert a registered tool into OpenAI function-calling schema format.
    
    Args:
        tool: Tool dictionary from tool manager
        
    Returns:
        OpenAI function-calling schema
    """
    # Convert parameter info to JSON schema
    properties = {}
    required = []
    
    # Get the parameters schema from the tool
    params_schema = tool['parameters']
    
    # Handle the case where parameters is already in OpenAI format
    if 'properties' in params_schema:
        properties = params_schema['properties']
        required = params_schema.get('required', [])
    else:
        # Legacy format - convert each parameter
        for param_name, param_info in params_schema.items():
            param_type = param_info.get('type', 'string')
            
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
            
            if param_info.get('required', False):
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
    
    return openai_tool


# Global instance using module-level singleton pattern
tool_manager = ToolManager()

def get_tools():
    """Get list of all available tools"""
    return tool_manager.getTools()

def use_tool(tool_name: str, **kwargs):
    """Execute a tool by name with provided arguments"""
    return tool_manager.useTool(tool_name, **kwargs)

def list_tools() -> str:
    """Get formatted string of available tools"""
    return tool_manager.list_tools()
