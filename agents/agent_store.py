"""Agent store for managing named agent definitions as JSON files"""

import json
import os
import pathlib
from typing import Dict, List, Optional
from datetime import datetime


class AgentStore:
    """
    Manages agent definitions stored as JSON files in a directory.
    Each agent is a named persona with a system prompt and tool list.
    """
    
    def __init__(self, store_dir: str = "agents/store"):
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
        current_time = datetime.utcnow().isoformat() + "Z"
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