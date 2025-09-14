import inspect
import importlib
from typing import Dict, List, Any, Callable, Optional, Tuple
from generators.world_generator import WorldGenerator
from generators.character_generator import CharacterGenerator
from tools.project_manager import project_manager

class ToolManager:
    """
    Manages available tools from generator classes.
    Automatically discovers and provides access to generation methods.
    """
    
    def __init__(self):
        self._tools_registry: Dict[str, Dict[str, Any]] = {}
        self._discover_tools()
    
    def _discover_tools(self):
        """Automatically discover tools from generator classes"""
        
        # World Generator tools
        world_gen_methods = self._get_generator_methods(WorldGenerator)
        for method_name, method_info in world_gen_methods.items():
            tool_name = f"world.{method_name}"
            self._tools_registry[tool_name] = {
                'name': tool_name,
                'description': method_info['description'],
                'parameters': method_info['parameters'],
                'generator_class': 'WorldGenerator',
                'method_name': method_name,
                'category': 'world_generation'
            }
        
        # Character Generator tools  
        char_gen_methods = self._get_generator_methods(CharacterGenerator)
        for method_name, method_info in char_gen_methods.items():
            tool_name = f"character.{method_name}"
            self._tools_registry[tool_name] = {
                'name': tool_name,
                'description': method_info['description'],
                'parameters': method_info['parameters'],
                'generator_class': 'CharacterGenerator',
                'method_name': method_name,
                'category': 'character_generation'
            }
    
    def _get_generator_methods(self, generator_class) -> Dict[str, Dict[str, Any]]:
        """Extract public methods from a generator class"""
        methods = {}
        
        for name, method in inspect.getmembers(generator_class, predicate=inspect.isfunction):
            # Skip private methods and __init__
            if name.startswith('_') or name == '__init__':
                continue
                
            # Get method signature and docstring
            signature = inspect.signature(method)
            docstring = inspect.getdoc(method) or f"Execute {name} operation"
            
            # Extract parameter info
            params = {}
            for param_name, param in signature.parameters.items():
                if param_name == 'self':
                    continue
                params[param_name] = {
                    'type': param.annotation if param.annotation != param.empty else 'Any',
                    'default': param.default if param.default != param.empty else None,
                    'required': param.default == param.empty
                }
            
            methods[name] = {
                'description': docstring,
                'parameters': params,
                'signature': signature
            }
        
        return methods
    
    def getTools(self) -> List[Dict[str, Any]]:
        """Get list of all available tools"""
        return list(self._tools_registry.values())
    
    def get_tools_by_category(self, category: str) -> List[Dict[str, Any]]:
        """Get tools filtered by category"""
        return [tool for tool in self._tools_registry.values() 
                if tool.get('category') == category]
    
    def get_tool_info(self, tool_name: str) -> Optional[Dict[str, Any]]:
        """Get detailed information about a specific tool"""
        return self._tools_registry.get(tool_name)
    
    def useTool(self, tool_name: str, **kwargs) -> Any:
        """Execute a specific tool with provided arguments"""
        tool_info = self._tools_registry.get(tool_name)
        if not tool_info:
            raise ValueError(f"Tool '{tool_name}' not found")
        
        # Get current project to access generators
        current_project = project_manager.get_current_project()
        if not current_project:
            raise RuntimeError("No current project set. Use project_manager.set_current_project() first.")
        
        # Get the appropriate generator instance
        generator = self._get_generator_instance(tool_info['generator_class'], current_project)
        
        # Get the method to call
        method = getattr(generator, tool_info['method_name'])
        
        # Validate and filter arguments
        validated_kwargs = self._validate_arguments(tool_info, kwargs)
        
        # Execute the tool
        try:
            result = method(**validated_kwargs)
            return result
        except Exception as e:
            raise RuntimeError(f"Tool execution failed for '{tool_name}': {str(e)}")
    
    def _get_generator_instance(self, generator_class_name: str, project):
        """Get the appropriate generator instance from the project"""
        if generator_class_name == 'WorldGenerator':
            return project.world_gen
        elif generator_class_name == 'CharacterGenerator':
            # Character generator might need to be added to Project class
            if not hasattr(project, 'character_gen'):
                project.character_gen = CharacterGenerator()
            return project.character_gen
        else:
            raise ValueError(f"Unknown generator class: {generator_class_name}")
    
    def _validate_arguments(self, tool_info: Dict[str, Any], provided_kwargs: Dict[str, Any]) -> Dict[str, Any]:
        """Validate and filter provided arguments against tool parameters"""
        parameters = tool_info['parameters']
        validated = {}
        
        # Check required parameters
        for param_name, param_info in parameters.items():
            if param_info['required'] and param_name not in provided_kwargs:
                raise ValueError(f"Missing required parameter '{param_name}' for tool '{tool_info['name']}'")
        
        # Filter and validate provided parameters
        for param_name, value in provided_kwargs.items():
            if param_name in parameters:
                validated[param_name] = value
            # Silently ignore extra parameters to be flexible
        
        return validated
    
    def list_tools(self, category: Optional[str] = None) -> str:
        """Get a formatted string listing available tools"""
        if category:
            tools = self.get_tools_by_category(category)
            header = f"Available {category} tools:"
        else:
            tools = self.getTools()
            header = "Available tools:"
        
        if not tools:
            return f"{header}\n  No tools found"
        
        lines = [header]
        for tool in tools:
            params = ", ".join([
                f"{name}{'*' if info['required'] else ''}" 
                for name, info in tool['parameters'].items()
            ])
            lines.append(f"  {tool['name']}({params}) - {tool['description']}")
        
        return "\n".join(lines)

tool_manager = ToolManager()

def get_tools():
    """Get list of all available tools"""
    return tool_manager.getTools()

def use_tool(tool_name: str, **kwargs):
    """Execute a tool by name with provided arguments"""
    return tool_manager.useTool(tool_name, **kwargs)

def list_tools(category: Optional[str] = None) -> str:
    """Get formatted string of available tools"""
    return tool_manager.list_tools(category)