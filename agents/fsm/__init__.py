"""
FSM (Finite State Machine) implementation for MaestroAgent.

This package contains the node-based state machine that orchestrates task execution
through explicit states: Planning → Executing → Validating → Compiling → Finished.

Each state is represented by a StateNode subclass that owns its complete logic,
making the execution flow clear and testable.
"""

from agents.fsm.state_node import StateNode, StateContext
from agents.fsm.planning_node import PlanningNode
from agents.fsm.executing_node import ExecutingNode
from agents.fsm.validating_node import ValidatingNode
from agents.fsm.compiling_node import CompilingNode
from agents.fsm.finished_node import FinishedNode

__all__ = [
    'StateNode',
    'StateContext',
    'PlanningNode',
    'ExecutingNode',
    'ValidatingNode',
    'CompilingNode',
    'FinishedNode',
]
