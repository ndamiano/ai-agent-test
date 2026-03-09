# AI Agent Framework

A generic agentic loop framework for building AI-powered applications with tool management, agent stores, and LMStudio integration.

## Features

- **Tool Manager**: Register and execute tools with proper parameter validation and error handling
- **Agent Store**: Persist and load agent configurations with custom system prompts and tool lists
- **Agent Spawner**: Create and manage multiple agent instances with different capabilities
- **LMStudio Connector**: Connect to LMStudio for local LLM inference with tool calling support
- **Proper Agentic Loop**: Implement robust tool calling with retry logic and error handling

## Architecture

The framework consists of several key components:

### Core Components

- **ToolManager**: Singleton pattern for managing available tools across the application
- **MainAgent**: Conversational agent that orchestrates tool calls in a proper agentic loop
- **AgentStore**: Persistent storage for agent configurations and metadata
- **AgentSpawner**: Factory for creating agents with specific tool sets and system prompts

### Connectors

- **LMStudio Client**: Local LLM inference with function calling support
- **Connector Selector**: Dynamic connector selection based on configuration

### Utilities

- **Logging Utils**: Structured logging for tool calls, agent decisions, and errors
- **Project Manager**: Singleton for managing current project context

## Usage

### Basic Agent Usage

```python
from agents.main_agent import MainAgent

# Create a basic agent
agent = MainAgent()
response = agent.chat("Hello, what can you do?")

# Create an agent with custom configuration from store
agent = MainAgent(agent_id="code-reviewer")
response = agent.chat("Review this code...")
```

### Tool Registration

```python
from tools.tool_manager import tool_manager

def my_tool(param1: str, param2: int = 0):
    """A sample tool"""
    return f"Result: {param1} with {param2}"

# Register the tool
tool_manager.register_tool(
    name="my_tool",
    description="A sample tool for demonstration",
    parameters={
        "type": "object",
        "properties": {
            "param1": {"type": "string"},
            "param2": {"type": "integer"}
        },
        "required": ["param1"]
    },
    fn=my_tool
)
```

### Agent Configuration

Agents can be configured and stored using the AgentStore:

```python
from agents.agent_store import AgentStore

store = AgentStore()
store.add(
    agent_id="code-reviewer",
    name="Code Reviewer",
    system_prompt="You are an expert code reviewer...",
    tools=["analyze_code", "check_style", "suggest_improvements"]
)
```

## Installation

1. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```

2. Configure LMStudio connection in `.env`:
   ```
   LMSTUDIO_HOST=localhost
   LMSTUDIO_PORT=1234
   ```

3. Run the demo:
   ```bash
   python demo_agent_store.py
   ```

## Contributing

This framework is designed to be extensible. You can:
- Add new connectors for different LLM providers
- Implement additional tool types
- Extend the agent store with new metadata
- Create specialized agent types for specific domains
