import inspect
import logging
from typing import Any, Callable, Dict, List, Optional, Union, get_type_hints

from .execution_context import get_execution_context

logger = logging.getLogger(__name__)

_INJECTED_PARAMS = frozenset({'task_id', 'subtask_id'})

_PY_TO_JSON = {
    str: 'string',
    int: 'integer',
    float: 'number',
    bool: 'boolean',
    list: 'array',
    dict: 'object',
}


def _schema_from_fn(fn: Callable, param_hints: Dict[str, Any]) -> Dict[str, Any]:
    try:
        hints = get_type_hints(fn)
    except Exception:
        hints = {}
    sig = inspect.signature(fn)
    properties = {}
    required = []

    for pname, param in sig.parameters.items():
        if pname in _INJECTED_PARAMS:
            continue

        hint = param_hints.get(pname)
        if isinstance(hint, dict):
            properties[pname] = hint
        else:
            annotation = hints.get(pname, inspect.Parameter.empty)
            origin = getattr(annotation, '__origin__', None)
            args = getattr(annotation, '__args__', ())
            if origin is Union and type(None) in args:
                inner = next(a for a in args if a is not type(None))
                json_type = _PY_TO_JSON.get(inner, 'string')
            elif annotation not in (inspect.Parameter.empty, type(None)):
                json_type = _PY_TO_JSON.get(annotation, 'string')
            else:
                json_type = 'string'
            prop = {'type': json_type}
            if isinstance(hint, str):
                prop['description'] = hint
            properties[pname] = prop

        if param.default is inspect.Parameter.empty:
            required.append(pname)

    return {'type': 'object', 'properties': properties, 'required': required}


class ToolManager:
    def __init__(self):
        self._tools_registry: Dict[str, Dict[str, Any]] = {}

    def register_tool(self, name: str, description: str, parameters: Dict[str, Any], fn: Callable, auto_inject_context: bool = True) -> None:
        self._tools_registry[name] = {
            'name': name,
            'description': description,
            'parameters': parameters,
            'function': fn,
            'auto_inject_context': auto_inject_context,
        }

    def tool(self, description: str, auto_inject_context: bool = True, param_hints: Dict[str, Any] = None, name: str = None):
        def decorator(fn):
            tool_name = name or fn.__name__
            parameters = _schema_from_fn(fn, param_hints or {})
            self.register_tool(tool_name, description, parameters, fn, auto_inject_context)
            return fn
        return decorator

    def getTools(self) -> List[Dict[str, Any]]:
        return list(self._tools_registry.values())

    def get_tool_info(self, tool_name: str) -> Optional[Dict[str, Any]]:
        return self._tools_registry.get(tool_name)

    def useTool(self, tool_name: str, **kwargs) -> Any:
        tool_info = self._tools_registry.get(tool_name)
        if not tool_info:
            available = ', '.join(sorted(self._tools_registry.keys()))
            raise ValueError(f"Tool '{tool_name}' not found. Available tools: {available}")

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
            return fn(**validated_kwargs)
        except Exception as e:
            raise RuntimeError(f"Tool execution failed for '{tool_name}': {str(e)}")

    def _validate_arguments(self, tool_info: Dict[str, Any], provided_kwargs: Dict[str, Any]) -> Dict[str, Any]:
        parameters = tool_info['parameters'].get('properties', {})
        required_params = tool_info['parameters'].get('required', [])
        fn_params = inspect.signature(tool_info['function']).parameters

        for param_name in required_params:
            if param_name not in provided_kwargs:
                raise ValueError(f"Missing required parameter '{param_name}' for tool '{tool_info['name']}'")

        return {k: v for k, v in provided_kwargs.items() if k in parameters or k in fn_params}


def build_openai_tool_schema(tool: dict) -> dict:
    params_schema = tool['parameters']
    return {
        'type': 'function',
        'function': {
            'name': tool['name'],
            'description': tool['description'],
            'parameters': {
                'type': 'object',
                'properties': params_schema.get('properties', {}),
                'required': params_schema.get('required', []),
            }
        }
    }


tool_manager = ToolManager()
