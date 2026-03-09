from typing import Dict, List
from database.task_store import TaskStore


class ContextBuilder:
    def __init__(self, task_store: TaskStore):
        """
        Initialize the ContextBuilder with a reference to the task store.
        
        Args:
            task_store (TaskStore): The task store instance to retrieve context from
        """
        self.task_store = task_store

    def build_for_subtask(self, task_id: str, subtask: Dict) -> str:
        """
        Build the full context string an agent will receive alongside its goal.
        
        Args:
            task_id (str): The ID of the main task
            subtask (Dict): The subtask dictionary containing goal, context_keys, and depends_on
            
        Returns:
            str: Formatted context string with clear section headers
        """
        context_parts = []
        
        # 1. Add overall task goal
        task = self.task_store.get_task(task_id)
        if task and "goal" in task:
            context_parts.append(f"Overall task goal: {task['goal']}")
        
        # 2. Add semantically relevant context
        semantic_context = self.task_store.retrieve_context(
            task_id, subtask["goal"], k=5
        )
        if semantic_context:
            formatted_semantic = self.format_retrieved_chunks(semantic_context)
            if formatted_semantic:
                context_parts.append(f"Relevant context:\n{formatted_semantic}")
        
        # 3. Add explicitly requested context keys
        if "context_keys" in subtask and subtask["context_keys"]:
            context_keys = subtask["context_keys"]
            for key in context_keys:
                context_value = self.task_store.get_context(task_id, key)
                if context_value:
                    context_parts.append(f"Context key '{key}': {context_value}")
        
        # 4. Add dependency outputs
        if "depends_on" in subtask and subtask["depends_on"]:
            dependency_output = self.format_dependency_outputs(
                task_id, subtask["depends_on"]
            )
            if dependency_output:
                context_parts.append(f"Dependency outputs:\n{dependency_output}")
        
        # Join all parts with double newlines for readability
        return "\n\n".join(context_parts)

    def format_retrieved_chunks(self, chunks: List[Dict]) -> str:
        """
        Format the raw results from retrieve_context into a readable block.
        
        Args:
            chunks (List[Dict]): List of context chunks with 'key' and 'value' fields
            
        Returns:
            str: Formatted context chunks or empty string if no chunks
        """
        if not chunks:
            return ""
        
        formatted_chunks = []
        for chunk in chunks:
            key = chunk.get("key", "")
            value = chunk.get("value", "")
            if key and value:
                formatted_chunks.append(f"Key: {key}\n{value}")
        
        return "\n\n".join(formatted_chunks)

    def format_dependency_outputs(self, task_id: str, depends_on: List[str]) -> str:
        """
        Format outputs from completed dependency subtasks.
        
        Args:
            task_id (str): The ID of the main task
            depends_on (List[str]): List of subtask IDs this subtask depends on
            
        Returns:
            str: Formatted dependency outputs or empty string if none
        """
        if not depends_on:
            return ""
        
        formatted_outputs = []
        for subtask_id in depends_on:
            subtask = self.task_store.get_subtask(subtask_id)
            if (
                subtask
                and subtask.get("status") == "completed"
                and subtask.get("output") is not None
            ):
                agent_id = subtask.get("agent_id", "unknown")
                output = subtask["output"]
                formatted_outputs.append(f"Output from {agent_id}: {output}")
        
        return "\n\n".join(formatted_outputs)