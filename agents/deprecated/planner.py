"""Planner agent for decomposing user goals into ordered subtasks"""

import json
import logging
from typing import Dict, List
from agents.main_agent import MainAgent
from agents.agent_store import AgentStore
from database.task_store import TaskStore


class PlannerAgent:
    def __init__(self):
        self.agent_store = AgentStore()
        self.task_store = TaskStore()
        try:
            self.planner_agent = MainAgent(agent_id="planner")
        except KeyError as e:
            raise RuntimeError(
                f"Failed to initialize planner agent: {e}. "
                "Ensure agents/store/planner.json exists."
            ) from e

    def _build_agents_section(self) -> str:
        lines = ["AVAILABLE AGENTS:"]
        for agent in self.agent_store.list():
            tools = ", ".join(agent["tools"]) if agent["tools"] else "None"
            lines.append(f"- {agent['id']}: {agent['name']}")
            lines.append(f"  Description: {agent['description']}")
            lines.append(f"  Tools: {tools}\n")
        return "\n".join(lines)

    def _get_context_section(self, goal: str, task_id: str) -> str:
        try:
            from connectors.embedding_client import embedding_client
            from database.vector_store import retrieve_global
            entries = retrieve_global(embedding_client.embed(goal), k=3)
        except Exception as e:
            logging.warning(f"Semantic search failed, falling back to keyword search: {e}")
            entries = self.task_store._keyword_search_context(task_id, goal, k=3)

        if not entries:
            return ""
        lines = ["\nPOTENTIALLY RELEVANT CONTEXT FROM PAST TASKS:"]
        lines += [f"- {e['key']}: {e['value']}" for e in entries]
        return "\n".join(lines)

    def _build_prompt(self, goal: str, task_id: str) -> str:
        return (
            f"USER GOAL: {goal}\n\n"
            f"{self._build_agents_section()}"
            f"{self._get_context_section(goal, task_id)}\n\n"
            "IMPORTANT: Output ONLY valid JSON. No explanations, no preamble."
        )

    def _parse_response(self, response: str) -> Dict:
        first, last = response.find("{"), response.rfind("}")
        if first == -1 or last == -1 or first >= last:
            raise ValueError(f"No JSON found in response: {response[:200]}")
        try:
            return json.loads(response[first : last + 1])
        except json.JSONDecodeError as e:
            raise ValueError(f"Failed to parse planner response: {response}") from e

    def _commit_plan(self, plan: Dict, task_id: str) -> List[Dict]:
        subtasks_data = sorted(plan.get("subtasks", []), key=lambda x: x["position"])
        created, position_to_id = [], {}

        for data in subtasks_data:
            subtask = self.task_store.create_subtask(
                task_id=task_id,
                agent_id=data["agent_id"],
                goal=data["goal"],
                position=data["position"],
                depends_on=None,
                input_context=None,
            )
            position_to_id[data["position"]] = subtask["id"]
            created.append(subtask)

        for data, subtask in zip(subtasks_data, created):
            positions = data.get("depends_on", [])
            if not positions:
                continue
            missing = [p for p in positions if p not in position_to_id]
            if missing:
                raise ValueError(
                    f"Planner referenced unknown positions: {missing}. "
                    f"Known: {list(position_to_id.keys())}"
                )
            self.task_store.update_subtask_depends_on(
                subtask["id"], [position_to_id[p] for p in positions]
            )

        self.task_store.log_event(task_id, "task_planned", f"Created {len(created)} subtasks")
        return created

    def run(self, goal: str, task_id: str) -> List[Dict]:
        try:
            self.task_store.update_task_status(task_id, "planning")
            plan = self._parse_response(self.planner_agent.chat(self._build_prompt(goal, task_id)))
            subtasks = self._commit_plan(plan, task_id)
            self.task_store.update_task_status(task_id, "in_progress")
            return subtasks
        except Exception as e:
            self.task_store.update_task_status(task_id, "failed")
            self.task_store.log_event(task_id, "task_failed", f"Planning failed: {e}")
            raise