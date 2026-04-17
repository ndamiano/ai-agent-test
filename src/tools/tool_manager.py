from typing import Dict, List, Any, Callable, Optional
import inspect
from functools import wraps

import logging
from .execution_context import get_execution_context

logger = logging.getLogger(__name__)


class ToolManager:
    def __init__(self):
        self._tools_registry: Dict[str, Dict[str, Any]] = {}

    def register_tool(self, name: str, description: str, parameters: Dict[str, Any], fn: Callable, auto_inject_context: bool = True) -> None:
        self._tools_registry[name] = {
            'name': name,
            'description': description,
            'parameters': parameters,
            'function': fn,
            'auto_inject_context': auto_inject_context
        }
    
    def getTools(self) -> List[Dict[str, Any]]:
        return list(self._tools_registry.values())

    def get_tool_info(self, tool_name: str) -> Optional[Dict[str, Any]]:
        return self._tools_registry.get(tool_name)

    def useTool(self, tool_name: str, **kwargs) -> Any:
        tool_info = self._tools_registry.get(tool_name)
        if not tool_info:
            available = ", ".join(sorted(self._tools_registry.keys()))
            error_msg = f"Tool '{tool_name}' not found. Available tools: {available}"
            logger.error(error_msg)
            raise ValueError(error_msg)

        fn = tool_info['function']

        if tool_info.get('auto_inject_context', True):
            context = get_execution_context()
            fn_params = inspect.signature(fn).parameters
            if context['task_id'] is not None and 'task_id' in fn_params and 'task_id' not in kwargs:
                kwargs['task_id'] = context['task_id']
            if context['subtask_id'] is not None and 'subtask_id' in fn_params and 'subtask_id' not in kwargs:
                kwargs['subtask_id'] = context['subtask_id']

        validated_kwargs = self._validate_arguments(tool_info, kwargs)

        try:
            result = fn(**validated_kwargs)
            return result
        except Exception as e:
            error_msg = f"Tool execution failed for '{tool_name}': {str(e)}"
            logger.error(error_msg)
            raise RuntimeError(error_msg)
    
    def _validate_arguments(self, tool_info: Dict[str, Any], provided_kwargs: Dict[str, Any]) -> Dict[str, Any]:
        parameters = tool_info['parameters'].get('properties', {})
        required_params = tool_info['parameters'].get('required', [])
        fn_params = inspect.signature(tool_info['function']).parameters

        for param_name in required_params:
            if param_name not in provided_kwargs:
                raise ValueError(f"Missing required parameter '{param_name}' for tool '{tool_info['name']}'")

        return {k: v for k, v in provided_kwargs.items() if k in parameters or k in fn_params}

    @staticmethod
    def tool(name: str, description: str, parameters: Dict[str, Any], auto_inject_context: bool = True):
        def decorator(fn):
            @wraps(fn)
            def wrapper(*args, **kwargs):
                return fn(*args, **kwargs)
            tool_manager.register_tool(name, description, parameters, fn, auto_inject_context)
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
