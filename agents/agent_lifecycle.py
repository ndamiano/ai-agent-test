"""
Agent Lifecycle Manager

Manages agent usage tracking, quality scoring, and automatic cleanup.
"""

import logging
from typing import List, Dict, Any, Optional
from datetime import datetime, timedelta
from agents.agent_store import AgentStore
from database.task_store import TaskStore
from config.protected_agents import is_protected_agent

logger = logging.getLogger(__name__)


class AgentLifecycleManager:
    """
    Manages the lifecycle of agents including usage tracking,
    quality scoring, and automatic cleanup.
    """

    # Default quality thresholds
    DEFAULT_MIN_QUALITY_SCORE = 40.0
    DEFAULT_MAX_IDLE_DAYS = 30

    def __init__(self, agent_store: AgentStore, task_store: TaskStore):
        """
        Initialize the lifecycle manager.

        Args:
            agent_store: AgentStore instance for persisting agents
            task_store: TaskStore instance for tracking task execution
        """
        self.agent_store = agent_store
        self.task_store = task_store

    def register_agent_usage(
        self,
        agent_id: str,
        task_id: str,
        subtask_id: str,
        success: bool,
        execution_time_ms: int
    ) -> None:
        """
        Track agent usage and update metrics after task execution.

        Args:
            agent_id: ID of the agent that executed the task
            task_id: ID of the parent task
            subtask_id: ID of the subtask
            success: Whether the subtask completed successfully
            execution_time_ms: Execution time in milliseconds
        """
        try:
            # Update agent metrics
            self.agent_store.update_metrics(agent_id, success, execution_time_ms)

            # Calculate and update quality score
            quality_score = self.calculate_quality_score(agent_id)
            agent = self.agent_store.get(agent_id)

            if 'metadata' in agent and 'quality_metrics' in agent['metadata']:
                agent['metadata']['quality_metrics']['quality_score'] = quality_score
                self.agent_store.save(agent)

            logger.info(
                f"Registered usage for agent '{agent_id}': "
                f"success={success}, quality_score={quality_score:.1f}"
            )

        except Exception as e:
            logger.error(f"Failed to register agent usage for '{agent_id}': {e}")

    def calculate_quality_score(self, agent_id: str) -> float:
        """
        Calculate a 0-100 quality score based on:
        - Success rate (60% weight)
        - Usage count (20% weight)
        - Recency (20% weight)

        Args:
            agent_id: ID of the agent to score

        Returns:
            Quality score from 0.0 to 100.0
        """
        agent = self.agent_store.get(agent_id)
        metadata = agent.get('metadata', {})
        metrics = metadata.get('quality_metrics', {})

        # Get metrics with defaults
        tasks_completed = metrics.get('tasks_completed', 0)
        tasks_failed = metrics.get('tasks_failed', 0)
        last_used_at = metrics.get('last_used_at')

        total_tasks = tasks_completed + tasks_failed

        # Success component (0-60 points)
        if total_tasks > 0:
            success_rate = tasks_completed / total_tasks
            success_component = success_rate * 60.0
        else:
            success_component = 0.0

        # Usage component (0-20 points)
        # Caps at 10 tasks for full score
        usage_component = min(total_tasks / 10.0, 1.0) * 20.0

        # Recency component (0-20 points)
        # Decays linearly over 30 days
        if last_used_at:
            try:
                # Parse ISO timestamp
                last_used = datetime.fromisoformat(last_used_at.replace('Z', '+00:00'))
                now = datetime.now(last_used.tzinfo)
                days_since_use = (now - last_used).days

                recency_component = max(0, 20.0 - (days_since_use / 30.0) * 20.0)
            except (ValueError, TypeError):
                recency_component = 0.0
        else:
            recency_component = 0.0

        total_score = success_component + usage_component + recency_component
        return round(total_score, 2)

    def identify_cleanup_candidates(self) -> List[Dict[str, Any]]:
        """
        Identify temporary agents that are candidates for cleanup based on:
        - Quality score below threshold
        - Idle time exceeding threshold
        - Not protected

        Returns:
            List of agent dictionaries that should be cleaned up
        """
        candidates = []
        temporary_agents = self.agent_store.get_agents_by_lifecycle('temporary')

        for agent in temporary_agents:
            agent_id = agent['id']

            # Skip protected agents
            if self.agent_store.is_protected(agent_id):
                continue

            metadata = agent.get('metadata', {})
            auto_cleanup = metadata.get('auto_cleanup', {})

            # Check if auto cleanup is enabled
            if not auto_cleanup.get('enabled', False):
                continue

            # Get thresholds
            min_quality = auto_cleanup.get('min_quality_score', self.DEFAULT_MIN_QUALITY_SCORE)
            max_idle_days = auto_cleanup.get('max_idle_days', self.DEFAULT_MAX_IDLE_DAYS)

            # Calculate current metrics
            quality_score = self.calculate_quality_score(agent_id)
            metrics = metadata.get('quality_metrics', {})
            last_used_at = metrics.get('last_used_at')

            # Check quality threshold
            if quality_score < min_quality and metrics.get('tasks_completed', 0) + metrics.get('tasks_failed', 0) >= 5:
                candidates.append({
                    'agent': agent,
                    'reason': f'Low quality score: {quality_score:.1f} < {min_quality}',
                    'quality_score': quality_score
                })
                continue

            # Check idle threshold
            if last_used_at:
                try:
                    last_used = datetime.fromisoformat(last_used_at.replace('Z', '+00:00'))
                    now = datetime.now(last_used.tzinfo)
                    days_idle = (now - last_used).days

                    if days_idle > max_idle_days:
                        candidates.append({
                            'agent': agent,
                            'reason': f'Idle for {days_idle} days > {max_idle_days} days',
                            'quality_score': quality_score
                        })
                except (ValueError, TypeError):
                    pass

        return candidates

    def cleanup_agent(self, agent_id: str, reason: str) -> bool:
        """
        Delete an agent with audit logging.

        Args:
            agent_id: ID of the agent to delete
            reason: Reason for deletion (for audit trail)

        Returns:
            True if deleted successfully, False otherwise
        """
        try:
            # Final safety check - do not delete protected agents
            if self.agent_store.is_protected(agent_id):
                logger.warning(f"Attempted to delete protected agent '{agent_id}'. Skipping.")
                return False

            self.agent_store.delete(agent_id)
            logger.info(f"Cleaned up agent '{agent_id}': {reason}")
            return True

        except Exception as e:
            logger.error(f"Failed to cleanup agent '{agent_id}': {e}")
            return False

    def cleanup_task_temporary_agents(self, task_id: str) -> int:
        """
        Cleanup all temporary agents that were created specifically for a task.

        Args:
            task_id: ID of the task that completed

        Returns:
            Number of agents cleaned up
        """
        cleanup_count = 0
        all_agents = self.agent_store.list()

        for agent in all_agents:
            metadata = agent.get('metadata', {})

            # Skip if not temporary
            if metadata.get('lifecycle') != 'temporary':
                continue

            # Skip if protected
            if self.agent_store.is_protected(agent['id']):
                continue

            # Check if created for this task
            creation_context = metadata.get('creation_context', {})
            if creation_context.get('task_id') == task_id:
                if self.cleanup_agent(agent['id'], f"Task {task_id} completed"):
                    cleanup_count += 1

        return cleanup_count

    def run_automatic_cleanup(self) -> int:
        """
        Run automatic cleanup for all agents that meet cleanup criteria.

        This should be called periodically (e.g., daily cron job).

        Returns:
            Number of agents cleaned up
        """
        candidates = self.identify_cleanup_candidates()
        cleanup_count = 0

        for candidate in candidates:
            agent_id = candidate['agent']['id']
            reason = candidate['reason']

            if self.cleanup_agent(agent_id, reason):
                cleanup_count += 1

        if cleanup_count > 0:
            logger.info(f"Automatic cleanup completed: {cleanup_count} agents removed")

        return cleanup_count
