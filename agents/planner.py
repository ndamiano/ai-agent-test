"""Planner agent for decomposing user goals into ordered subtasks"""

import json
import sqlite3
import os
from datetime import datetime
from typing import Dict, List, Any, Optional
from agents.main_agent import MainAgent
from agents.agent_store import AgentStore
from database.task_store import task_store
from tools.logging_utils import log_agent_decision, log_error


class PlannerAgent:
    """
    Planner agent responsible for building input to the planner agent 
    and parsing its output into the task store.
    """
    
    def __init__(self):
        """Initialize the planner agent with references to stores."""
        self.agent_store = AgentStore()
        self.task_store = task_store
        self.planner_agent = MainAgent(agent_id="planner")
    
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
        
        # Retrieve relevant context from past tasks
        context_entries = self.task_store.retrieve_context(task_id, goal, k=3)
        
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
            
            # Strip markdown code fences if present
            if response.startswith("```json"):
                response = response[7:]  # Remove ```json
            if response.endswith("```"):
                response = response[:-3]  # Remove ```
            
            # Parse JSON response
            plan = json.loads(response)
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
        
        # Create all subtasks first
        for subtask_data in subtasks:
            subtask = self.task_store.create_subtask(
                task_id=task_id,
                agent_id=subtask_data["agent_id"],
                goal=subtask_data["goal"],
                position=subtask_data["position"],
                depends_on=subtask_data.get("depends_on", []),
                input_context=None
            )
            created_subtasks.append(subtask)
        
        # Build position to ID map
        position_to_id = {subtask["position"]: subtask["id"] for subtask in created_subtasks}
        
        # Update depends_on with real subtask IDs
        for subtask in created_subtasks:
            depends_on_positions = subtask.get("depends_on", [])
            if depends_on_positions:
                real_depends_on = [position_to_id[pos] for pos in depends_on_positions if pos in position_to_id]
                # Update the subtask in the database
                with sqlite3.connect(os.environ.get("TASK_DB_PATH", "data/tasks.db")) as conn:
                    conn.execute("""
                        UPDATE subtasks 
                        SET depends_on = ?, updated_at = ?
                        WHERE id = ?
                    """, (json.dumps(real_depends_on), datetime.utcnow().isoformat(), subtask["id"]))
                    conn.commit()
        
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