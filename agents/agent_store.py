"""Agent store for managing named agent definitions as JSON files

Agent definitions (id, name, description, system_prompt, tools) are stored in JSON files.
Runtime metrics (tasks_completed, success_rate, etc.) are stored in the database.
This separation ensures agent definitions can be version controlled without git noise.
"""

import json
import os
import pathlib
import sqlite3
from typing import Dict, List, Optional
from config.time_utils import get_utc_timestamp
from config.protected_agents import is_protected_agent
from database.schema import get_db_path, configure_connection


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
    
    def _merge_metrics(self, agent: Dict) -> Dict:
        """
        Merge runtime metrics from database into agent dictionary.

        Args:
            agent: Agent dictionary from JSON file

        Returns:
            Agent dictionary with metrics merged into metadata
        """
        metrics = self.get_metrics(agent['id'])
        if metrics:
            if 'metadata' not in agent:
                agent['metadata'] = {}
            agent['metadata']['quality_metrics'] = metrics
        return agent

    def get(self, agent_id: str, include_metrics: bool = True) -> Dict:
        """
        Load an agent definition by ID.

        Args:
            agent_id: Unique identifier of the agent
            include_metrics: Whether to include runtime metrics from database

        Returns:
            Agent dictionary (with metrics merged if include_metrics=True)

        Raises:
            KeyError: If agent with given ID doesn't exist
        """
        file_path = self.store_dir / f"{agent_id}.json"

        if not file_path.exists():
            raise KeyError(f"Agent '{agent_id}' not found")

        with open(file_path, 'r', encoding='utf-8') as f:
            agent = json.load(f)

        if include_metrics:
            agent = self._merge_metrics(agent)

        return agent
    
    def list(self, include_metrics: bool = True) -> List[Dict]:
        """
        List all available agents.

        Args:
            include_metrics: Whether to include runtime metrics from database

        Returns:
            List of all agent dictionaries (with metrics merged if include_metrics=True)
        """
        agents = []

        for json_file in self.store_dir.glob("*.json"):
            try:
                with open(json_file, 'r', encoding='utf-8') as f:
                    agent = json.load(f)
                    if include_metrics:
                        agent = self._merge_metrics(agent)
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

    def get_agent_by_id(self, agent_id: str, include_metrics: bool = True) -> Optional[Dict]:
        """
        Get an agent by ID without raising an error if not found.

        Args:
            agent_id: Unique identifier of the agent
            include_metrics: Whether to include runtime metrics from database

        Returns:
            Agent dictionary if found, None otherwise
        """
        try:
            return self.get(agent_id, include_metrics=include_metrics)
        except KeyError:
            return None

    def get_metrics(self, agent_id: str) -> Optional[Dict]:
        """
        Get runtime metrics for an agent from the database.

        Args:
            agent_id: Unique identifier of the agent

        Returns:
            Metrics dictionary or None if no metrics exist
        """
        db_path = get_db_path()
        with sqlite3.connect(db_path) as conn:
            configure_connection(conn)
            cursor = conn.execute(
                "SELECT tasks_completed, tasks_failed, success_rate, last_used_at, quality_score "
                "FROM agent_metrics WHERE agent_id = ?",
                (agent_id,)
            )
            row = cursor.fetchone()
            if row:
                return {
                    'tasks_completed': row[0],
                    'tasks_failed': row[1],
                    'success_rate': row[2],
                    'last_used_at': row[3],
                    'quality_score': row[4]
                }
            return None

    def update_metrics(self, agent_id: str, success: bool, execution_time_ms: int = 0) -> None:
        """
        Update quality metrics for an agent after task execution.
        Metrics are stored in the database, not in the JSON file.

        Args:
            agent_id: Unique identifier of the agent
            success: Whether the task completed successfully
            execution_time_ms: Execution time in milliseconds (unused currently)

        Raises:
            KeyError: If agent doesn't exist
        """
        # Verify agent exists
        if not self.exists(agent_id):
            raise KeyError(f"Agent '{agent_id}' not found")

        db_path = get_db_path()
        current_time = get_utc_timestamp() + "Z"

        with sqlite3.connect(db_path) as conn:
            configure_connection(conn)

            # Get current metrics or initialize
            cursor = conn.execute(
                "SELECT tasks_completed, tasks_failed FROM agent_metrics WHERE agent_id = ?",
                (agent_id,)
            )
            row = cursor.fetchone()

            if row:
                # Update existing metrics
                tasks_completed = row[0] + (1 if success else 0)
                tasks_failed = row[1] + (0 if success else 1)
            else:
                # Initialize new metrics
                tasks_completed = 1 if success else 0
                tasks_failed = 0 if success else 1

            # Calculate success rate
            total_tasks = tasks_completed + tasks_failed
            success_rate = (tasks_completed / total_tasks * 100) if total_tasks > 0 else 0.0

            # Calculate quality score (simple formula based on success rate and volume)
            # Can be enhanced later with more sophisticated scoring
            quality_score = success_rate * (1 + min(total_tasks / 100, 1))

            # Upsert metrics
            if row:
                conn.execute(
                    """UPDATE agent_metrics
                       SET tasks_completed = ?, tasks_failed = ?, success_rate = ?,
                           last_used_at = ?, quality_score = ?, updated_at = ?
                       WHERE agent_id = ?""",
                    (tasks_completed, tasks_failed, success_rate, current_time,
                     quality_score, current_time, agent_id)
                )
            else:
                conn.execute(
                    """INSERT INTO agent_metrics
                       (agent_id, tasks_completed, tasks_failed, success_rate,
                        last_used_at, quality_score, created_at, updated_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                    (agent_id, tasks_completed, tasks_failed, success_rate,
                     current_time, quality_score, current_time, current_time)
                )
            conn.commit()

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