import os
import logging
import asyncio
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Dict, List, Any, Optional
from dataclasses import dataclass
from enum import Enum
import time

from database.task_store import TaskStore
from agents.agent_store import AgentStore
from tools.logging_utils import tool_logger

class ExecutionMode(Enum):
    SEQUENTIAL = "sequential"
    PARALLEL = "parallel"

@dataclass
class Subtask:
    id: str
    agent_id: str
    goal: str
    status: str
    position: int
    context_keys: List[str]
    retry_failed: bool

class Orchestrator:
    def __init__(self):
        """Initialize the orchestrator with task store, agent store, and executor."""
        self.task_store = TaskStore()
        self.agent_store = AgentStore()
        self.logger = tool_logger
        
        # Get max parallel workers from environment, default to 2
        max_workers = int(os.getenv('MAX_PARALLEL_WORKERS', '2'))
        self._executor = ThreadPoolExecutor(max_workers=max_workers)
        
        self.logger.info(f"Orchestrator initialized with {max_workers} max parallel workers")

    def run_task(self, task_id: str) -> None:
        """
        Main entry point for running a task.
        
        Args:
            task_id: The ID of the task to run
            
        Raises:
            Exception: Re-raises any unhandled exceptions after logging
        """
        try:
            self.logger.info(f"Starting task execution for task_id: {task_id}")
            
            # Load the task
            task = self.task_store.get_task(task_id)
            if not task:
                raise ValueError(f"Task {task_id} not found")
            
            # Get execution mode and route accordingly
            execution_mode = task.get('execution_mode', 'sequential')
            
            if execution_mode == 'sequential':
                self._run_sequential(task_id)
            elif execution_mode == 'parallel':
                self._run_parallel(task_id)
            else:
                raise ValueError(f"Unknown execution mode: {execution_mode}")
                
        except Exception as e:
            self.logger.error(f"Task {task_id} failed with error: {str(e)}")
            self.task_store.update_task_status(task_id, 'failed')
            self.logger.info(f"Task {task_id} marked as failed")
            raise

    def _run_sequential(self, task_id: str) -> None:
        """
        Execute subtasks sequentially, one at a time.
        
        Args:
            task_id: The ID of the task to run
        """
        self.logger.info(f"Running task {task_id} in sequential mode")
        
        # Get all subtasks ordered by position
        subtasks = self.task_store.get_subtasks(task_id)
        subtasks.sort(key=lambda x: x.get('position', 0))
        
        for subtask in subtasks:
            try:
                self.logger.info(f"Executing subtask {subtask['id']} for task {task_id}")
                output = self._execute_subtask(subtask)
                self.logger.info(f"Subtask {subtask['id']} completed successfully")
                
            except Exception as e:
                self.logger.error(f"Subtask {subtask['id']} failed: {str(e)}")
                
                # Check if we should retry
                retry_failed = subtask.get('retry_failed', False)
                if retry_failed:
                    self.logger.info(f"Retrying subtask {subtask['id']}")
                    try:
                        output = self._execute_subtask(subtask)
                        self.logger.info(f"Subtask {subtask['id']} retry successful")
                    except Exception as retry_e:
                        self.logger.error(f"Subtask {subtask['id']} retry failed: {str(retry_e)}")
                        self.task_store.update_task_status(task_id, 'failed')
                        return
                else:
                    self.task_store.update_task_status(task_id, 'failed')
                    return
        
        # All subtasks completed successfully
        self.task_store.update_task_status(task_id, 'completed')
        self.logger.info(f"Task {task_id} completed successfully")

    def _run_parallel(self, task_id: str) -> None:
        """
        Execute subtasks in parallel, respecting dependencies.
        
        Args:
            task_id: The ID of the task to run
        """
        self.logger.info(f"Running task {task_id} in parallel mode")
        
        # Track in-flight subtasks to avoid double submission
        in_flight_subtasks = set()
        futures_to_subtask = {}
        
        # Keep checking for ready subtasks until all are done
        while True:
            # Get ready subtasks
            ready_subtasks = self.task_store.get_ready_subtasks(task_id)
            
            # Submit ready subtasks that aren't already in flight
            for subtask in ready_subtasks:
                if subtask['id'] not in in_flight_subtasks:
                    self.logger.info(f"Submitting subtask {subtask['id']} for parallel execution")
                    future = self._executor.submit(self._execute_subtask, subtask)
                    futures_to_subtask[future] = subtask
                    in_flight_subtasks.add(subtask['id'])
            
            # If no futures, we're done
            if not futures_to_subtask:
                break
                
            # Wait for at least one future to complete
            completed_futures = []
            for future in as_completed(futures_to_subtask):
                completed_futures.append(future)
                break  # Wait for at least one completion
            
            # Process completed futures
            for future in completed_futures:
                subtask = futures_to_subtask.pop(future)
                in_flight_subtasks.remove(subtask['id'])
                
                try:
                    result = future.result()
                    self.logger.info(f"Subtask {subtask['id']} completed successfully")
                except Exception as e:
                    self.logger.error(f"Subtask {subtask['id']} failed: {str(e)}")
                    self.task_store.update_task_status(task_id, 'failed')
                    return
        
        # All subtasks completed successfully
        self.task_store.update_task_status(task_id, 'completed')
        self.logger.info(f"Task {task_id} completed successfully")

    def _execute_subtask(self, subtask: Dict) -> str:
        """
        Execute a single subtask.
        
        Args:
            subtask: The subtask dictionary containing id, agent_id, goal, etc.
            
        Returns:
            The output string from the agent execution
            
        Raises:
            Exception: Re-raises any exceptions after logging and updating status
        """
        subtask_id = subtask['id']
        
        try:
            # Update subtask status to in_progress
            self.task_store.update_subtask_status(subtask_id, 'in_progress')
            self.logger.info(f"Subtask {subtask_id} started")
            
            # Load agent definition
            agent_id = subtask['agent_id']
            agent_definition = self.agent_store.get_agent(agent_id)
            if not agent_definition:
                raise ValueError(f"Agent {agent_id} not found")
            
            # Build context for the agent
            context_text = ""
            
            # Retrieve context from task store
            retrieved_context = self.task_store.retrieve_context(
                subtask['task_id'], 
                subtask['goal'], 
                k=5
            )
            
            if retrieved_context:
                context_text += "Retrieved context:\n"
                for i, ctx in enumerate(retrieved_context, 1):
                    context_text += f"{i}. {ctx}\n"
                context_text += "\n"
            
            # Add explicitly listed context keys
            context_keys = subtask.get('context_keys', [])
            if context_keys:
                context_text += "Explicit context:\n"
                for key in context_keys:
                    context_value = self.task_store.get_context(key)
                    if context_value:
                        context_text += f"- {key}: {context_value}\n"
                context_text += "\n"
            
            # Instantiate the agent
            from agents.main_agent import MainAgent
            agent = MainAgent(agent_id)
            
            # Build the message with context
            message = f"{context_text}Task: {subtask['goal']}"
            
            # Execute the agent
            output = agent.chat(message)
            
            # Store the output
            self.task_store.set_subtask_output(subtask_id, output)
            
            # Log completion
            output_preview = output[:100] + "..." if len(output) > 100 else output
            self.logger.info(f"Subtask {subtask_id} completed. Output preview: {output_preview}")
            
            return output
            
        except Exception as e:
            # Update subtask status to failed
            self.task_store.update_subtask_status(subtask_id, 'failed')
            self.logger.error(f"Subtask {subtask_id} failed: {str(e)}")
            raise