"""Agent spawner for creating sub-agents at runtime"""

from typing import List, Dict, Any, Optional
from agents.main_agent import MainAgent
from tools.tool_manager import tool_manager


class AgentSpawner:
    """
    Spawns sub-agents with specified tools for task delegation.
    Prevents infinite recursion by not allowing sub-agents to spawn further agents.
    """
    
    def __init__(self):
        """Initialize the agent spawner"""
        pass
    
    def spawn_agent(self, task: str, tools: Optional[str] = None) -> str:
        """
        Create and run a sub-agent with specified tools on a task.
        
        Args:
            task: The task for the sub-agent to perform
            tools: Comma-separated list of tool names to give the sub-agent.
                  If None or empty, the sub-agent gets all available tools.
                  
        Returns:
            The result from the sub-agent's execution
        """
        # Parse tools list
        available_tools = tool_manager.getTools()
        tool_names = [tool['name'] for tool in available_tools]
        
        if tools:
            requested_tools = [t.strip() for t in tools.split(',') if t.strip()]
            # Validate requested tools exist
            for tool_name in requested_tools:
                if tool_name not in tool_names:
                    raise ValueError(f"Unknown tool: {tool_name}")
            selected_tools = requested_tools
        else:
            # Give all tools if none specified
            selected_tools = tool_names
        
        # Create a fresh MainAgent instance
        sub_agent = MainAgent()
        
        # Register only the specified tools on the sub-agent
        # We need to rebuild the tools schema with only the selected tools
        self._configure_sub_agent_tools(sub_agent, selected_tools)
        
        try:
            # Run the agent loop on the task
            result = sub_agent.chat(task)
            return result
        except Exception as e:
            return f"Sub-agent execution failed: {str(e)}"
    
    def _configure_sub_agent_tools(self, sub_agent: MainAgent, selected_tool_names: List[str]):
        """
        Configure the sub-agent to only have access to specified tools.
        
        Args:
            sub_agent: The sub-agent instance to configure
            selected_tool_names: List of tool names the sub-agent should have access to
        """
        # Get all tools and filter to only the selected ones
        all_tools = tool_manager.getTools()
        selected_tools = [tool for tool in all_tools if tool['name'] in selected_tool_names]
        
        # Create a temporary tool manager with only the selected tools
        # We'll rebuild the tools schema manually for the sub-agent
        openai_tools = []
        
        for tool in selected_tools:
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
            
            openai_tools.append(openai_tool)
        
        # Set the filtered tools schema on the sub-agent
        sub_agent._tools_schema = openai_tools


# Register the agent spawner as a tool
def _spawn_agent_impl(task: str, tools: Optional[str] = None) -> str:
    """Implementation function for the spawn_agent tool"""
    spawner = AgentSpawner()
    return spawner.spawn_agent(task, tools)


# Register the tool with the tool manager
tool_manager.register_tool(
    name="spawn_agent",
    description="Create and run a sub-agent with specified tools on a task",
    parameters={
        "type": "object",
        "properties": {
            "task": {
                "type": "string",
                "description": "The task for the sub-agent to perform"
            },
            "tools": {
                "type": "string",
                "description": "Comma-separated list of tool names to give the sub-agent (optional)"
            }
        },
        "required": ["task"]
    },
    fn=_spawn_agent_impl
)