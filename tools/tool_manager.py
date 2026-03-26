"""Generic tool manager for registering and executing tools in an agentic loop"""

from typing import Dict, List, Any, Callable, Optional
import inspect
from threading import Lock
from functools import wraps

from .logging_utils import log_tool_call, log_error
from .execution_context import get_execution_context


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
    
    def register_tool(self, name: str, description: str, parameters: Dict[str, Any], fn: Callable, auto_inject_context: bool = True) -> None:
        """
        Register a tool explicitly with the manager.

        Args:
            name: Unique tool name
            description: Tool description
            parameters: Parameter schema in OpenAI function calling format
            fn: Function to execute when tool is called
            auto_inject_context: Whether to automatically inject task_id/subtask_id (default: True)
        """
        self._tools_registry[name] = {
            'name': name,
            'description': description,
            'parameters': parameters,
            'function': fn,
            'auto_inject_context': auto_inject_context
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

        # Auto-inject execution context if enabled for this tool
        if tool_info.get('auto_inject_context', True):
            context = get_execution_context()
            # Only inject if context exists and function accepts these parameters
            fn_params = inspect.signature(fn).parameters
            if context['task_id'] is not None and 'task_id' in fn_params:
                # Only inject if not already provided by caller
                if 'task_id' not in kwargs:
                    kwargs['task_id'] = context['task_id']
            if context['subtask_id'] is not None and 'subtask_id' in fn_params:
                if 'subtask_id' not in kwargs:
                    kwargs['subtask_id'] = context['subtask_id']

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

        # Validate and filter provided parameters
        # Note: We now accept both declared parameters AND auto-injected context parameters
        fn = tool_info['function']
        fn_params = inspect.signature(fn).parameters

        for param_name, value in provided_kwargs.items():
            if param_name in parameters or param_name in fn_params:
                validated[param_name] = value
            # Silently ignore other extra parameters to be flexible

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

    @classmethod
    def tool(cls, name: str, description: str, parameters: Dict[str, Any], auto_inject_context: bool = True):
        """
        Decorator for registering tools with the tool manager.

        Args:
            name: Unique tool name
            description: Tool description
            parameters: Parameter schema in OpenAI function calling format
            auto_inject_context: Whether to auto-inject execution context

        Returns:
            Decorator function
        """
        def decorator(fn):
            @wraps(fn)
            def wrapper(*args, **kwargs):
                return fn(*args, **kwargs)

            # Get the singleton instance
            instance = cls._instance if cls._instance is not None else cls()
            instance.register_tool(name, description, parameters, fn, auto_inject_context)
            return wrapper

        return decorator


def build_openai_tool_schema(tool: dict) -> dict:
    """
    Convert a registered tool into OpenAI function-calling schema format.
    """
    params_schema = tool['parameters']

    return {
        "type": "function",
        "function": {
            "name": tool['name'],
            "description": tool['description'],
            "parameters": {
                "type": "object",
                "properties": params_schema.get('properties', {}),
                "required": params_schema.get('required', []),
            }
        }
    }


# Global instance using module-level singleton pattern
tool_manager = ToolManager()
