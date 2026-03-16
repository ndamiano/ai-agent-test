"""Agent store for managing named agent definitions as JSON files"""

import json
import os
import pathlib
from typing import Dict, List, Optional
from config.time_utils import get_utc_timestamp
from config.protected_agents import is_protected_agent


class AgentStore:
    """
    Manages agent definitions stored as JSON files in a directory.
    Each agent is a named persona with a system prompt and tool list.
    """
    
    def __init__(self, store_dir: str = "config/agents"):
        """
        Initialize the agent store.

        Args:
            store_dir: Directory to store agent JSON files (relative to project root)
        """
        self.store_dir = pathlib.Path(store_dir)
        self.store_dir.mkdir(parents=True, exist_ok=True)
    
    def save(self, agent: Dict) -> None:
        """
        Save an agent definition to a JSON file.
        
        Args:
            agent: Agent dictionary with id, name, description, system_prompt, tools
            
        Raises:
            ValueError: If agent is missing required fields
        """
        # Validate required fields
        required_fields = ['id', 'name', 'description', 'system_prompt', 'tools']
        for field in required_fields:
            if field not in agent:
                raise ValueError(f"Agent missing required field: {field}")
        
        # Validate tools is a list
        if not isinstance(agent['tools'], list):
            raise ValueError("Agent tools must be a list")
        
        # Set/update timestamps
        current_time = get_utc_timestamp() + "Z"
        agent['updated_at'] = current_time
        
        # Set created_at if this is a new agent
        if 'created_at' not in agent:
            agent['created_at'] = current_time
        
        # Write to file
        file_path = self.store_dir / f"{agent['id']}.json"
        with open(file_path, 'w', encoding='utf-8') as f:
            json.dump(agent, f, indent=2, ensure_ascii=False)
    
    def get(self, agent_id: str) -> Dict:
        """
        Load an agent definition by ID.
        
        Args:
            agent_id: Unique identifier of the agent
            
        Returns:
            Agent dictionary
            
        Raises:
            KeyError: If agent with given ID doesn't exist
        """
        file_path = self.store_dir / f"{agent_id}.json"
        
        if not file_path.exists():
            raise KeyError(f"Agent '{agent_id}' not found")
        
        with open(file_path, 'r', encoding='utf-8') as f:
            return json.load(f)
    
    def list(self) -> List[Dict]:
        """
        List all available agents.
        
        Returns:
            List of all agent dictionaries
        """
        agents = []
        
        for json_file in self.store_dir.glob("*.json"):
            try:
                with open(json_file, 'r', encoding='utf-8') as f:
                    agent = json.load(f)
                    agents.append(agent)
            except json.JSONDecodeError:
                # Log warning for invalid JSON files
                import logging
                logging.warning(f"Skipping invalid JSON file: {json_file}")
                continue
        
        return agents
    
    def delete(self, agent_id: str) -> None:
        """
        Delete an agent definition.
        
        Args:
            agent_id: Unique identifier of the agent to delete
            
        Raises:
            KeyError: If agent with given ID doesn't exist
        """
        file_path = self.store_dir / f"{agent_id}.json"
        
        if not file_path.exists():
            raise KeyError(f"Agent '{agent_id}' not found")
        
        file_path.unlink()
    
    def exists(self, agent_id: str) -> bool:
        """
        Check if an agent exists.

        Args:
            agent_id: Unique identifier to check

        Returns:
            True if agent exists, False otherwise
        """
        file_path = self.store_dir / f"{agent_id}.json"
        return file_path.exists()

    def get_agent_by_id(self, agent_id: str) -> Optional[Dict]:
        """
        Get an agent by ID without raising an error if not found.

        Args:
            agent_id: Unique identifier of the agent

        Returns:
            Agent dictionary if found, None otherwise
        """
        try:
            return self.get(agent_id)
        except KeyError:
            return None

    def update_metrics(self, agent_id: str, success: bool, execution_time_ms: int) -> None:
        """
        Update quality metrics for an agent after task execution.

        Args:
            agent_id: Unique identifier of the agent
            success: Whether the task completed successfully
            execution_time_ms: Execution time in milliseconds

        Raises:
            KeyError: If agent doesn't exist
        """
        agent = self.get(agent_id)

        # Initialize metadata if not present
        if 'metadata' not in agent:
            agent['metadata'] = {}

        # Initialize quality_metrics if not present
        if 'quality_metrics' not in agent['metadata']:
            agent['metadata']['quality_metrics'] = {
                'tasks_completed': 0,
                'tasks_failed': 0,
                'success_rate': 0.0,
                'last_used_at': None,
                'quality_score': 0.0
            }

        metrics = agent['metadata']['quality_metrics']

        # Update counts
        if success:
            metrics['tasks_completed'] += 1
        else:
            metrics['tasks_failed'] += 1

        # Update success rate
        total_tasks = metrics['tasks_completed'] + metrics['tasks_failed']
        metrics['success_rate'] = (metrics['tasks_completed'] / total_tasks * 100) if total_tasks > 0 else 0.0

        # Update last used timestamp
        metrics['last_used_at'] = get_utc_timestamp() + "Z"

        # Save updated agent
        self.save(agent)

    def get_agents_by_lifecycle(self, lifecycle: str) -> List[Dict]:
        """
        Get all agents with a specific lifecycle type.

        Args:
            lifecycle: Either 'permanent' or 'temporary'

        Returns:
            List of agent dictionaries matching the lifecycle
        """
        all_agents = self.list()
        return [
            agent for agent in all_agents
            if agent.get('metadata', {}).get('lifecycle', 'permanent') == lifecycle
        ]

    def is_protected(self, agent_id: str) -> bool:
        """
        Check if an agent is protected from deletion.

        Args:
            agent_id: Unique identifier of the agent

        Returns:
            True if agent is protected, False otherwise
        """
        # Check global protected list
        if is_protected_agent(agent_id):
            return True

        # Check agent metadata
        agent = self.get_agent_by_id(agent_id)
        if agent and agent.get('metadata', {}).get('is_protected', False):
            return True

        return False