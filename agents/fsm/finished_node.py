"""
Finished state node: Terminal state indicating task completion.
"""

from agents.fsm.state_node import StateNode, StateContext


class FinishedNode(StateNode):
    """
    FINISHED state: Terminal state - task complete.

    Entry: Synthesis has completed
    Exit: None (terminal)
    Transition: None (terminal)
    """

    @property
    def state_name(self) -> str:
        return "FINISHED"

    async def execute_async(self, context: StateContext) -> 'StateNode':
        """
        This should never be called - the FSM loop checks for FinishedNode
        before calling execute_async().
        """
        raise RuntimeError("FINISHED node should not be executed")
