import os
import logging
import asyncio
import queue
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Dict, List, Any, Optional, Callable
import time
from datetime import datetime

from database.task_store import TaskStore
from agents.agent_store import AgentStore
from tools.logging_utils import tool_logger

class Orchestrator:
    def __init__(self, broadcast_fn: Optional[Callable] = None):
        """Initialize the orchestrator with task store, agent store, and executor."""
        self.task_store = TaskStore()
        self.agent_store = AgentStore()
        self.logger = tool_logger
        self.broadcast_fn = broadcast_fn

        # Get max parallel workers from environment, default to 2
        max_workers = int(os.getenv('MAX_PARALLEL_WORKERS', '2'))
        self._executor = ThreadPoolExecutor(max_workers=max_workers)

        self.logger.log_agent_decision("Orchestrator initialization", f"Initialized with {max_workers} max parallel workers")

    def _run_sequential(self, task_id: str, broadcast_fn: Optional[Callable] = None) -> None:
        """
        Execute subtasks sequentially, respecting dependencies.
        
        Args:
            task_id: The ID of the task to run
            broadcast_fn: Optional callback function to broadcast events to WebSocket clients
        """
        self.logger.info(f"Running task {task_id} in sequential mode")
        
        # Use the same dependency-aware approach as parallel mode
        while True:
            # Get ready subtasks (this respects dependencies)
            ready_subtasks = self.task_store.get_ready_subtasks(task_id)
            
            if not ready_subtasks:
                # No more ready subtasks, check if all are completed
                all_subtasks = self.task_store.get_subtasks_for_task(task_id)
                pending_subtasks = [s for s in all_subtasks if s['status'] != 'completed']
                
                if pending_subtasks:
                    self.logger.error(f"Task {task_id} has pending subtasks but no ready subtasks: {[s['id'] for s in pending_subtasks]}")
                    self.task_store.update_task_status(task_id, 'failed')
                    raise RuntimeError(f"Task {task_id} has pending subtasks but no ready subtasks")
                else:
                    break  # All subtasks completed successfully
            
            # Execute the first ready subtask
            subtask = ready_subtasks[0]
            try:
                self.logger.info(f"Executing subtask {subtask['id']} for task {task_id}")
                output = self._execute_subtask(subtask, broadcast_fn)
                self.logger.info(f"Subtask {subtask['id']} completed successfully")
                
            except Exception as e:
                self.logger.error(f"Subtask {subtask['id']} failed: {str(e)}")
                
                # Check if we should retry - remove dead retry logic since retry_failed field is never set
                # The retry_failed field is not wired from ExecutionConfig to subtasks, so this branch is dead code
                self.task_store.update_task_status(task_id, 'failed')
                return
        
        # All subtasks completed successfully
        self.task_store.update_task_status(task_id, 'completed')
        self.logger.info(f"Task {task_id} completed successfully")

    def _run_parallel(self, task_id: str, broadcast_fn: Optional[Callable] = None) -> None:
        """
        Execute subtasks in parallel, respecting dependencies.
        
        Args:
            task_id: The ID of the task to run
            broadcast_fn: Optional callback function to broadcast events to WebSocket clients
        """
        self.logger.info(f"Running task {task_id} in parallel mode")
        
        # Track in-flight subtasks to avoid double submission
        in_flight_subtasks = set()
        futures_to_subtask = {}
        iteration_count = 0
        max_iterations = 100  # Prevent infinite loops
        
        try:
            # Keep checking for ready subtasks until all are done
            while True:
                iteration_count += 1
                if iteration_count > max_iterations:
                    self.logger.error(f"Task {task_id} exceeded maximum iterations ({max_iterations}), possible deadlock")
                    self.task_store.update_task_status(task_id, 'failed')
                    raise RuntimeError(f"Task {task_id} exceeded maximum iterations, possible deadlock")
                
                # Get ready subtasks
                ready_subtasks = self.task_store.get_ready_subtasks(task_id)
                
                # Submit ready subtasks that aren't already in flight
                for subtask in ready_subtasks:
                    if subtask['id'] not in in_flight_subtasks:
                        # Remove pre-submission status update - trust _execute_subtask to set in_progress when it actually starts
                        self.logger.info(f"Submitting subtask {subtask['id']} for parallel execution")
                        future = self._executor.submit(self._execute_subtask, subtask, broadcast_fn)
                        futures_to_subtask[future] = subtask
                        in_flight_subtasks.add(subtask['id'])
                
                # If no futures, check if all subtasks are actually completed
                if not futures_to_subtask:
                    # Verify all subtasks are completed before marking task as completed
                    all_subtasks = self.task_store.get_subtasks_for_task(task_id)
                    pending_subtasks = [s for s in all_subtasks if s['status'] != 'completed']
                    
                    if pending_subtasks:
                        self.logger.error(f"Task {task_id} has pending subtasks but no futures to process: {[s['id'] for s in pending_subtasks]}")
                        self.task_store.update_task_status(task_id, 'failed')
                        raise RuntimeError(f"Task {task_id} has pending subtasks but no futures to process")
                    else:
                        break  # All subtasks completed successfully
                
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
                        raise
        
        except Exception as e:
            # On failure, cancel remaining futures to prevent thread leaks
            for future in futures_to_subtask:
                future.cancel()
            raise
        
        # Note: Not shutting down the shared executor here to allow reuse across tasks
        # The executor will be properly cleaned up when the Orchestrator instance is destroyed
        
        # All subtasks completed successfully
        self.task_store.update_task_status(task_id, 'completed')
        self.logger.info(f"Task {task_id} completed successfully")

    def _execute_subtask(self, subtask: Dict, broadcast_fn: Optional[Callable] = None) -> str:
        """
        Execute a single subtask.

        Args:
            subtask: The subtask dictionary containing id, agent_id, goal, etc.
            broadcast_fn: Optional callback function to broadcast events to WebSocket clients

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
            if broadcast_fn:
                try:
                    broadcast_fn({
                        'type': 'subtask_started',
                        'message': f"Subtask {subtask_id} started",
                        'task_id': subtask['task_id'],
                        'subtask_id': subtask_id,
                        'timestamp': datetime.now().isoformat()
                    })
                except Exception as e:
                    self.logger.error(f"Broadcast error: {e}")

            # Load agent definition
            agent_id = subtask['agent_id']
            try:
                agent_definition = self.agent_store.get(agent_id)
            except KeyError:
                raise ValueError(f"Agent {agent_id} not found")

            # Re-fetch the subtask from the DB immediately before building context
            # to ensure we have the latest dependency information
            fresh_subtask = self.task_store.get_subtask(subtask_id)

            # Use ContextBuilder to build the context
            from agents.context_builder import ContextBuilder
            context_builder = ContextBuilder(self.task_store)
            context_text = context_builder.build_for_subtask(fresh_subtask['task_id'], fresh_subtask)

            # Instantiate the agent
            from agents.main_agent import MainAgent
            agent = MainAgent(agent_id)

            # Build the message with context
            message = f"{context_text}\n\nTask: {fresh_subtask['goal']}"

            # Execute the agent
            output = agent.chat(message)

            # Store the output and update status to completed
            self.task_store.set_subtask_output(subtask_id, output)

            # Log completion
            output_preview = output[:100] + "..." if len(output) > 100 else output
            self.logger.info(f"Subtask {subtask_id} completed. Output preview: {output_preview}")
            if broadcast_fn:
                broadcast_fn({
                    'type': 'subtask_completed',
                    'message': f"Subtask {subtask_id} completed",
                    'task_id': subtask['task_id'],
                    'subtask_id': subtask_id,
                    'timestamp': datetime.now().isoformat()
                })

            return output

        except Exception as e:
            # Update subtask status to failed
            self.task_store.update_subtask_status(subtask_id, 'failed')
            self.logger.error(f"Subtask {subtask_id} failed: {str(e)}")
            if broadcast_fn:
                broadcast_fn({
                    'type': 'subtask_failed',
                    'message': f"Subtask {subtask_id} failed: {str(e)}",
                    'task_id': subtask['task_id'],
                    'subtask_id': subtask_id,
                    'timestamp': datetime.now().isoformat()
                })
            raise

    def run_task(self, task_id: str, broadcast_fn: Optional[Callable] = None) -> None:
        """
        Main entry point for running a task.

        Args:
            task_id: The ID of the task to run
            broadcast_fn: Optional callback function to broadcast events to WebSocket clients

        Raises:
            Exception: Re-raises any unhandled exceptions after logging
        """
        try:
            self.logger.info(f"Starting task execution for task_id: {task_id}")

            # Load the task
            task = self.task_store.get_task(task_id)

            # Get execution mode and route accordingly
            execution_mode = task.get('execution_mode', 'sequential')

            if execution_mode == 'sequential':
                self._run_sequential(task_id, broadcast_fn)
            elif execution_mode == 'parallel':
                self._run_parallel(task_id, broadcast_fn)
            else:
                raise ValueError(f"Unknown execution mode: {execution_mode}")

        except Exception as e:
            self.logger.error(f"Task {task_id} failed with error: {str(e)}")
            raise
