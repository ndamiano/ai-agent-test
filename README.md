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

### LLM Clients

- **LMStudio Client**: Local LLM inference with function calling support
- **Connector Selector**: Dynamic connector selection based on configuration (located in `llm_clients/`)

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

2. Start the backend:
   ```bash
   python run.py
   ```

   On first run, `config/settings.json` will be created automatically with default settings.

3. (Optional) Build the frontend:
   ```bash
   cd frontend
   npm install
   npm run dev
   ```

## Configuration

Settings are managed through `config/settings.json`, which is automatically created on first run and **gitignored** to avoid committing environment-specific configuration.

### Settings File

**Location**: `config/settings.json` (gitignored)
**Template**: `config/settings.example.json`

The settings file contains:
```json
{
  "connector_type": "lmstudio",
  "lmstudio": {
    "base_url": "http://localhost:1234",
    "model": "local-model",
    "temperature": 0.7,
    "max_tokens": 50000
  }
}
```

### Configuring via UI

The easiest way to configure settings is through the web UI:
1. Start the backend server
2. Open the frontend
3. Click the gear icon (⚙️) in the top-right corner
4. Modify your LMStudio connection settings

### Environment Variables (Fallback)

If `config/settings.json` doesn't exist or is missing values, the system falls back to environment variables:

- `LMSTUDIO_BASE_URL` - LMStudio server URL (default: `http://localhost:1234`)
- `LMSTUDIO_MODEL` - Model identifier (default: `local-model`)

**Note**: The `.env` file is also gitignored and should not be committed.

## Contributing

This framework is designed to be extensible. You can:
- Add new connectors for different LLM providers
- Implement additional tool types
- Extend the agent store with new metadata
- Create specialized agent types for specific domains
