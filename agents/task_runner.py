from typing import Dict, Any, Optional
import threading
import logging
from agents.planner import PlannerAgent
from agents.orchestrator import Orchestrator
from database.task_store import task_store
from agents.context_builder import ContextBuilder
from agents.main_agent import MainAgent
from agents.agent_store import AgentStore

logger = logging.getLogger(__name__)

class TaskRunner:
    def __init__(self):
        self.planner = PlannerAgent()
        self.orchestrator = Orchestrator()
        self.context_builder = ContextBuilder(task_store)
        self.main_agent = MainAgent()
        
    def execution_mode_from_goal(self, goal: str) -> str:
        """Determine execution mode based on goal content."""
        goal_lower = goal.lower()
        if any(keyword in goal_lower for keyword in ['code', 'program', 'script', 'build', 'develop']):
            return 'sequential'
        elif any(keyword in goal_lower for keyword in ['story', 'write', 'narrative', 'plot', 'chapter']):
            return 'sequential'
        elif any(keyword in goal_lower for keyword in ['world', 'setting', 'universe', 'map']):
            return 'sequential'
        else:
            return 'sequential'
    
    def create_and_run(self, goal: str, execution_mode: str = None) -> str:
        """Create and run a task synchronously."""
        if execution_mode is None:
            execution_mode = self.execution_mode_from_goal(goal)
            
        # Create task
        task_id = task_store.create_task(goal, execution_mode)
        logger.info(f"Task created: {task_id}")
        
        # Plan the task
        self.planner.run(goal, task_id)
        logger.info(f"Task planned: {task_id}")
        
        # Execute the task
        self.orchestrator.run_task(task_id)
        logger.info(f"Task completed: {task_id}")
        
        return task_id
    
    def create_and_run_background(self, goal: str, execution_mode: str = None) -> str:
        """Create and run a task asynchronously in the background."""
        if execution_mode is None:
            execution_mode = self.execution_mode_from_goal(goal)
        
        # Default to sequential if execution_mode is not sequential or parallel
        if execution_mode not in ['sequential', 'parallel']:
            execution_mode = 'sequential'
            
        # Create task
        task_id = task_store.create_task(goal, execution_mode)
        logger.info(f"Background task created: {task_id}")
        
        # Plan the task
        self.planner.run(goal, task_id)
        logger.info(f"Background task planned: {task_id}")
        
        # Execute in background thread
        def run_task():
            try:
                self.orchestrator.run_task(task_id)
                logger.info(f"Background task completed: {task_id}")
            except Exception as e:
                logger.error(f"Background task failed: {task_id}, error: {e}")
        
        thread = threading.Thread(target=run_task, daemon=True)
        thread.start()
        
        return task_id
    
    def get_status(self, task_id: str) -> Dict[str, Any]:
        """Get comprehensive status of a task."""
        try:
            task = task_store.get_task(task_id)
        except KeyError:
            return {"error": f"Task {task_id} not found"}
        
        subtasks = task_store.get_subtasks(task_id)
        events = task_store.get_events(task_id, limit=10)
        
        # Build summary
        total_subtasks = len(subtasks)
        completed_subtasks = len([s for s in subtasks if s['status'] == 'completed'])
        
        if total_subtasks == 0:
            progress_summary = "Task created, planning in progress"
        elif completed_subtasks == total_subtasks:
            progress_summary = f"Task completed successfully ({total_subtasks}/{total_subtasks} subtasks)"
        else:
            progress_summary = f"Task in progress ({completed_subtasks}/{total_subtasks} subtasks completed)"
        
        return {
            "task": task,
            "subtasks": subtasks,
            "events": events,
            "summary": progress_summary
        }
    
    def ask(self, task_id: str, question: str) -> str:
        """Query a task's accumulated context with a natural language question."""
        # Get relevant context chunks
        context_chunks = task_store.retrieve_context(task_id, question, k=5)
        
        if not context_chunks:
            return "No context found for this task. The task may not exist or have no recorded context yet."
        
        # Format context for the main agent
        context_text = "\n\n".join([
            f"Context chunk {i+1}:\n{chunk['value']}"
            for i, chunk in enumerate(context_chunks)
        ])
        
        # Create a prompt for the main agent
        prompt = f"""Answer the following question based on the provided context from task {task_id}:

Question: {question}

Context:
{context_text}

Please provide a concise, direct answer based solely on the context provided."""
        
        # Use the main agent to answer
        response = self.main_agent.run(prompt)
        return response

# Global instance
task_runner = TaskRunner()