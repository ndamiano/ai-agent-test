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
    
    def spawn_agent(self, task: str, tools: Optional[str] = None, agent_id: Optional[str] = None) -> str:
        """
        Create and run a sub-agent with specified tools or agent definition on a task.
        
        Args:
            task: The task for the sub-agent to perform
            tools: Comma-separated list of tool names to give the sub-agent.
                  If None or empty, the sub-agent gets all available tools.
            agent_id: Optional agent ID to load from agent store. If provided,
                     uses the agent's system prompt and tool list instead of tools parameter.
                  
        Returns:
            The result from the sub-agent's execution
        """
        # If agent_id is provided, load agent from store
        if agent_id:
            # Create sub-agent with the specified agent definition
            sub_agent = MainAgent(agent_id=agent_id)
        else:
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
        from tools.tool_manager import build_openai_tool_schema
        
        # Get all tools and filter to only the selected ones
        all_tools = tool_manager.getTools()
        selected_tools = [tool for tool in all_tools if tool['name'] in selected_tool_names]
        
        # Create a temporary tool manager with only the selected tools
        # We'll rebuild the tools schema manually for the sub-agent
        openai_tools = []
        
        for tool in selected_tools:
            openai_tool = build_openai_tool_schema(tool)
            openai_tools.append(openai_tool)
        
        # Set the filtered tools schema on the sub-agent
        sub_agent._tools_schema = openai_tools


# Register the agent spawner as a tool
def _spawn_agent_impl(task: str, tools: Optional[str] = None, agent_id: Optional[str] = None) -> str:
    """Implementation function for the spawn_agent tool"""
    spawner = AgentSpawner()
    return spawner.spawn_agent(task, tools, agent_id)


# Register the tool with the tool manager
tool_manager.register_tool(
    name="spawn_agent",
    description="Create and run a sub-agent with specified tools or agent definition on a task",
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
            },
            "agent_id": {
                "type": "string",
                "description": "Optional agent ID to load from agent store (optional)"
            }
        },
        "required": ["task"]
    },
    fn=_spawn_agent_impl
)
