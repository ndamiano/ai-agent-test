"""Planner agent for decomposing user goals into ordered subtasks"""

import json
import os
import re
from typing import Dict, List, Any, Optional
from agents.main_agent import MainAgent
from agents.agent_store import AgentStore
from database.task_store import TaskStore
from tools.logging_utils import log_agent_decision, log_error


class PlannerAgent:
    """
    Planner agent responsible for building input to the planner agent 
    and parsing its output into the task store.
    """
    
    def __init__(self):
        """Initialize the planner agent with references to stores."""
        self.agent_store = AgentStore()
        self.task_store = TaskStore()
        try:
            self.planner_agent = MainAgent(agent_id="planner")
        except KeyError as e:
            raise RuntimeError(f"Failed to initialize planner agent: {str(e)}. Please ensure the planner agent definition exists in agents/store/planner.json") from e
    
    def build_planning_prompt(self, goal: str, task_id: str) -> str:
        """
        Build the full prompt the planner LLM will receive.
        
        Args:
            goal: The user's original goal
            task_id: The ID of the task being planned
            
        Returns:
            Complete prompt string for the planner LLM
        """
        # Get all available agents from the agent store
        available_agents = self.agent_store.list()
        
        # Format agents list
        agents_section = "AVAILABLE AGENTS:\n"
        for agent in available_agents:
            agents_section += f"- {agent['id']}: {agent['name']}\n"
            agents_section += f"  Description: {agent['description']}\n"
            agents_section += f"  Tools: {', '.join(agent['tools']) if agent['tools'] else 'None'}\n\n"
        
        # Retrieve relevant context from past tasks (cross-task search)
        from database.vector_store import retrieve_global
        try:
            # Try to embed the goal text for semantic search
            from connectors.embedding_client import embedding_client
            query_embedding = embedding_client.embed(goal)
            # Retrieve similar context entries from all tasks
            context_entries = retrieve_global(query_embedding, k=3)
        except Exception as e:
            # If embedding fails, fall back to keyword-based search
            import logging
            logging.warning(f"Semantic search failed for planning context: {e}. Falling back to keyword search.")
            context_entries = self.task_store._keyword_search_context(task_id, goal, k=3)
        
        context_section = ""
        if context_entries:
            context_section = "\nPOTENTIALLY RELEVANT CONTEXT FROM PAST TASKS:\n"
            for entry in context_entries:
                context_section += f"- {entry['key']}: {entry['value']}\n"
        
        # Build the full prompt
        prompt = f"""USER GOAL: {goal}

{agents_section}{context_section}
IMPORTANT: Output ONLY the JSON plan structure. No explanations, no preamble, no formatting. Just the JSON.

OUTPUT FORMAT:
{{
  "execution_mode": "sequential" or "parallel",
  "summary": "one sentence describing the overall plan",
  "subtasks": [
    {{
      "position": 0,
      "agent_id": "agent_id",
      "goal": "specific instruction for this agent",
      "depends_on": [],
      "context_keys": []
    }}
  ]
}}

RULES:
- context_keys is a list of keys the planner expects to exist in the context store by the time this subtask runs
- depends_on is a list of positions (not IDs yet - IDs don't exist until the task store creates them) of subtasks that must complete first
- Be bold and specific. Don't create vague subtasks. Don't ask clarifying questions. Make a confident plan and commit to it
- ALWAYS output ONLY the JSON structure - no additional text, explanations, or formatting"""
        
        return prompt
    
    def plan(self, goal: str, task_id: str) -> Dict:
        """
        Execute the planning process and parse the JSON response.
        
        Args:
            goal: The user's original goal
            task_id: The ID of the task being planned
            
        Returns:
            Parsed plan dictionary
            
        Raises:
            ValueError: If JSON parsing fails
        """
        # Build the planning prompt
        prompt = self.build_planning_prompt(goal, task_id)
        
        try:
            # Send prompt to planner agent
            response = self.planner_agent.chat(prompt)
            
            # More robust extraction: find the first { and last } and slice between them
            first_brace = response.find('{')
            last_brace = response.rfind('}')
            
            if first_brace == -1 or last_brace == -1 or first_brace >= last_brace:
                raise ValueError(f"Could not find JSON structure in response: {response[:200]}...")
            
            json_str = response[first_brace:last_brace + 1]
            
            # Parse JSON response
            plan = json.loads(json_str)
            return plan
            
        except json.JSONDecodeError as e:
            raise ValueError(f"Failed to parse planner JSON response: {response}") from e
    
    def commit_plan(self, plan: Dict, task_id: str) -> List[Dict]:
        """
        Write the parsed plan to the task store.
        
        Args:
            plan: Parsed plan dictionary
            task_id: The ID of the task being planned
            
        Returns:
            List of created subtask dictionaries
        """
        subtasks = plan.get("subtasks", [])
        created_subtasks = []
        position_to_id = {}
        
        # First pass: create all subtasks with no depends_on
        for subtask_data in subtasks:
            subtask = self.task_store.create_subtask(
                task_id=task_id,
                agent_id=subtask_data["agent_id"],
                goal=subtask_data["goal"],
                position=subtask_data["position"],
                depends_on=None,
                input_context=None
            )
            position_to_id[subtask_data["position"]] = subtask["id"]
            created_subtasks.append(subtask)
        
        # Second pass: update depends_on with real IDs
        # Iterate through the original plan subtasks, not the created_subtasks
        for i, subtask_data in enumerate(subtasks):
            depends_on_positions = subtask_data.get("depends_on", [])
            if depends_on_positions:
                # Validate that all referenced positions exist
                missing_positions = [pos for pos in depends_on_positions if pos not in position_to_id]
                if missing_positions:
                    raise ValueError(f"Planner referenced non-existent positions: {missing_positions}. "
                                   f"Available positions: {list(position_to_id.keys())}")
                
                real_depends_on = [position_to_id[pos] for pos in depends_on_positions]
                # Update the subtask in the database using task_store method
                self.task_store.update_subtask_depends_on(created_subtasks[i]["id"], real_depends_on)
        
        # Log the planning event
        self.task_store.log_event(task_id, "task_planned", f"Created {len(created_subtasks)} subtasks")
        
        return created_subtasks
    
    def run(self, goal: str, task_id: str) -> List[Dict]:
        """
        Orchestrate the full planning flow.
        
        Args:
            goal: The user's original goal
            task_id: The ID of the task being planned
            
        Returns:
            List of created subtask dictionaries
            
        Raises:
            Exception: If planning fails, updates task status to failed and logs event
        """
        try:
            # Update task status to planning
            self.task_store.update_task_status(task_id, "planning")
            
            # Execute planning
            plan = self.plan(goal, task_id)
            
            # Commit plan to task store
            subtasks = self.commit_plan(plan, task_id)
            
            # Update task status to in_progress
            self.task_store.update_task_status(task_id, "in_progress")
            
            return subtasks
            
        except Exception as e:
            # On failure, update task status to failed and log event
            self.task_store.update_task_status(task_id, "failed")
            self.task_store.log_event(task_id, "task_failed", f"Planning failed: {str(e)}")
            raise