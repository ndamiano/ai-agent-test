"""
Agent Validator

Provides validation logic for agent specifications and operations.
Ensures agent integrity and safety before creation or deletion.
"""

import re
from typing import Dict, Any, Tuple, List
from config.protected_agents import is_protected_agent
from tools.tool_manager import tool_manager


class AgentValidator:
    """Validates agent specifications and operations"""

    # Validation constraints
    MIN_NAME_LENGTH = 3
    MAX_NAME_LENGTH = 100
    MIN_DESCRIPTION_LENGTH = 10
    MIN_SYSTEM_PROMPT_LENGTH = 50

    @staticmethod
    def validate_agent_spec(agent: Dict[str, Any]) -> Tuple[bool, str]:
        """
        Validate a complete agent specification.

        Args:
            agent: Dictionary containing agent specification

        Returns:
            Tuple of (is_valid, error_message)
            If valid, error_message will be empty string
        """
        # Check required fields
        required_fields = ['id', 'name', 'description', 'system_prompt', 'tools']
        missing_fields = [field for field in required_fields if field not in agent]
        if missing_fields:
            return False, f"Missing required fields: {', '.join(missing_fields)}"

        # Validate ID format (kebab-case)
        if not AgentValidator._is_valid_id(agent['id']):
            return False, f"Invalid agent ID '{agent['id']}'. Must be lowercase kebab-case (e.g., 'my-agent-name')"

        # Validate name length
        name = agent['name']
        if len(name) < AgentValidator.MIN_NAME_LENGTH:
            return False, f"Agent name must be at least {AgentValidator.MIN_NAME_LENGTH} characters"
        if len(name) > AgentValidator.MAX_NAME_LENGTH:
            return False, f"Agent name must be at most {AgentValidator.MAX_NAME_LENGTH} characters"

        # Validate description length
        description = agent['description']
        if len(description) < AgentValidator.MIN_DESCRIPTION_LENGTH:
            return False, f"Agent description must be at least {AgentValidator.MIN_DESCRIPTION_LENGTH} characters"

        # Validate system prompt length
        system_prompt = agent['system_prompt']
        if len(system_prompt) < AgentValidator.MIN_SYSTEM_PROMPT_LENGTH:
            return False, f"Agent system prompt must be at least {AgentValidator.MIN_SYSTEM_PROMPT_LENGTH} characters"

        # Validate tools array
        if not isinstance(agent['tools'], list):
            return False, "Agent tools must be a list"

        # Validate each tool exists
        invalid_tools = AgentValidator._validate_tools(agent['tools'])
        if invalid_tools:
            return False, f"Invalid tools: {', '.join(invalid_tools)}. These tools do not exist in the tool registry"

        # Validate metadata if present
        if 'metadata' in agent:
            valid, error = AgentValidator._validate_metadata(agent['metadata'])
            if not valid:
                return False, error

        return True, ""

    @staticmethod
    def validate_agent_name(name: str) -> Tuple[bool, str]:
        """
        Validate an agent name.

        Args:
            name: Proposed agent name

        Returns:
            Tuple of (is_valid, error_message)
        """
        if len(name) < AgentValidator.MIN_NAME_LENGTH:
            return False, f"Agent name must be at least {AgentValidator.MIN_NAME_LENGTH} characters"
        if len(name) > AgentValidator.MAX_NAME_LENGTH:
            return False, f"Agent name must be at most {AgentValidator.MAX_NAME_LENGTH} characters"
        return True, ""

    @staticmethod
    def can_delete_agent(agent_id: str, agent_store) -> Tuple[bool, str]:
        """
        Check if an agent can be safely deleted.

        Args:
            agent_id: ID of the agent to delete
            agent_store: AgentStore instance for checking existence

        Returns:
            Tuple of (can_delete, reason)
            If can_delete is False, reason explains why not
        """
        # Check if agent exists
        agent = agent_store.get_agent_by_id(agent_id)
        if not agent:
            return False, f"Agent '{agent_id}' does not exist"

        # Check if protected
        if is_protected_agent(agent_id):
            return False, f"Agent '{agent_id}' is a protected system agent and cannot be deleted"

        # Check metadata protection
        metadata = agent.get('metadata', {})
        if metadata.get('is_protected', False):
            return False, f"Agent '{agent_id}' is marked as protected in its metadata"

        return True, ""

    @staticmethod
    def generate_agent_id(name: str, agent_store) -> str:
        """
        Generate a unique kebab-case ID from an agent name.

        If the base ID already exists, appends -1, -2, etc. until unique.

        Args:
            name: Human-readable agent name
            agent_store: AgentStore instance for checking uniqueness

        Returns:
            Unique agent ID in kebab-case format
        """
        # Convert to kebab-case
        # Remove special characters, convert spaces and underscores to hyphens
        base_id = re.sub(r'[^\w\s-]', '', name.lower())
        base_id = re.sub(r'[\s_]+', '-', base_id)
        base_id = re.sub(r'-+', '-', base_id)  # Remove duplicate hyphens
        base_id = base_id.strip('-')  # Remove leading/trailing hyphens

        # Ensure uniqueness
        agent_id = base_id
        counter = 1
        while agent_store.get_agent_by_id(agent_id) is not None:
            agent_id = f"{base_id}-{counter}"
            counter += 1

        return agent_id

    @staticmethod
    def _is_valid_id(agent_id: str) -> bool:
        """
        Check if an agent ID follows the kebab-case format.

        Args:
            agent_id: ID to validate

        Returns:
            True if valid kebab-case, False otherwise
        """
        # Must be lowercase kebab-case (letters, numbers, hyphens)
        # Cannot start or end with hyphen
        pattern = r'^[a-z0-9]+(-[a-z0-9]+)*$'
        return bool(re.match(pattern, agent_id))

    @staticmethod
    def _validate_tools(tools: List[str]) -> List[str]:
        """
        Validate that all tools in the list exist in the tool registry.

        Args:
            tools: List of tool names

        Returns:
            List of invalid tool names (empty if all valid)
        """
        invalid_tools = []
        registered_tools = {tool['name'] for tool in tool_manager.getTools()}

        for tool_name in tools:
            if tool_name not in registered_tools:
                invalid_tools.append(tool_name)

        return invalid_tools

    @staticmethod
    def _validate_metadata(metadata: Dict[str, Any]) -> Tuple[bool, str]:
        """
        Validate agent metadata structure.

        Args:
            metadata: Metadata dictionary

        Returns:
            Tuple of (is_valid, error_message)
        """
        # Validate lifecycle if present
        if 'lifecycle' in metadata:
            if metadata['lifecycle'] not in ['permanent', 'temporary']:
                return False, f"Invalid lifecycle value '{metadata['lifecycle']}'. Must be 'permanent' or 'temporary'"

        # Validate is_protected if present
        if 'is_protected' in metadata:
            if not isinstance(metadata['is_protected'], bool):
                return False, "metadata.is_protected must be a boolean"

        # Validate created_by if present
        if 'created_by' in metadata:
            valid_creators = ['system', 'agent-designer', 'user']
            if metadata['created_by'] not in valid_creators:
                return False, f"Invalid created_by value '{metadata['created_by']}'. Must be one of: {', '.join(valid_creators)}"

        return True, ""
