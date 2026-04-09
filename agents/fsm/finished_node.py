from agents.fsm.state_node import StateNode, StateContext


class FinishedNode(StateNode):
    @property
    def state_name(self) -> str:
        return "FINISHED"

    async def execute_async(self, context: StateContext) -> 'StateNode':
        raise RuntimeError("FINISHED node should not be executed")
