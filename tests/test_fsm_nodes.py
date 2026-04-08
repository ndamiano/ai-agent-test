"""Unit tests for FSM state nodes."""
import unittest
import asyncio
from unittest.mock import Mock, MagicMock, patch, AsyncMock
from agents.fsm.state_node import StateContext
from agents.fsm.planning_node import PlanningNode
from agents.fsm.executing_node import ExecutingNode
from agents.fsm.validating_node import ValidatingNode
from agents.fsm.compiling_node import CompilingNode
from agents.fsm.finished_node import FinishedNode
class TestStateContext(unittest.TestCase):
    """Test StateContext dataclass."""
    def test_increment_wave_normal(self):
        """Test wave increment works normally."""
        context = StateContext(
            task_id="test",
            task_store=Mock(),
            agent_store=Mock(),
            wave_count=5,
            max_waves=20,
        )
        context.increment_wave()
        self.assertEqual(context.wave_count, 6)
    def test_increment_wave_exceeds_max(self):
        """Test that exceeding max_waves raises RuntimeError."""
        context = StateContext(
            task_id="test",
            task_store=Mock(),
            agent_store=Mock(),
            wave_count=20,
            max_waves=20,
        )
        with self.assertRaises(RuntimeError) as cm:
            context.increment_wave()
        self.assertIn("exceeded maximum wave limit", str(cm.exception))
class TestPlanningNode(unittest.IsolatedAsyncioTestCase):
    """Test PlanningNode."""
    def setUp(self):
        self.mock_task_store = Mock()
        self.mock_agent_store = Mock()
        self.mock_broadcast = Mock()
    async def test_planning_spawns_subtasks_transitions_to_executing(self):
        """Test that planning node spawns subtasks and transitions to EXECUTING."""
        context = StateContext(
            task_id="test-task-123",
            task_store=self.mock_task_store,
            agent_store=self.mock_agent_store,
        )
        # Track call count to simulate subtasks being spawned
        call_count = [0]
        def get_subtasks_side_effect(task_id):
            call_count[0] += 1
            if call_count[0] <= 2:  # First couple calls (before spawn)
                return []
            else:  # After spawn
                return [{
                    "id": "sub1",
                    "status": "pending",
                    "agent_id": "worker",
                    "position": 0,
                    "goal": "Test work",
                    "depends_on": [],
                }]
        self.mock_task_store.get_subtasks_for_task.side_effect = get_subtasks_side_effect
        self.mock_task_store.get_task.return_value = {
            "id": "test-task-123",
            "goal": "Test goal",
            "working_directory": "/tmp/test",
        }
        self.mock_task_store.get_all_context.return_value = {}
        # Mock agent store
        self.mock_agent_store.get.return_value = {
            "system_prompt": "You are Maestro. {{AGENT_ROSTER}}",
        }
        self.mock_agent_store.list.return_value = []
        # Mock MainAgent
        with patch('agents.main_agent.MainAgent') as mock_main_agent:
            mock_agent_instance = Mock()
            mock_agent_instance.chat.return_value = "Planning complete"
            mock_main_agent.return_value = mock_agent_instance
            # Execute node
            node = PlanningNode()
            next_node = await node.execute_async(context)
            # Verify MainAgent was instantiated and called
            mock_main_agent.assert_called_once()
            mock_agent_instance.chat.assert_called_once()
            # Verify transition to ExecutingNode
            self.assertIsInstance(next_node, ExecutingNode)
    async def test_planning_no_subtasks_skips_to_compiling(self):
        """Test that if Maestro doesn't spawn anything, we skip to COMPILING."""
        context = StateContext(
            task_id="test-task-123",
            task_store=self.mock_task_store,
            agent_store=self.mock_agent_store,
        )
        # Mock no subtasks spawned
        self.mock_task_store.get_subtasks_for_task.return_value = []
        self.mock_task_store.get_task.return_value = {
            "id": "test-task-123",
            "goal": "Test goal",
            "working_directory": "/tmp/test",
        }
        self.mock_task_store.get_all_context.return_value = {}
        # Mock agent store
        self.mock_agent_store.get.return_value = {
            "system_prompt": "You are Maestro. {{AGENT_ROSTER}}",
        }
        self.mock_agent_store.list.return_value = []
        # Mock MainAgent
        with patch('agents.main_agent.MainAgent') as mock_main_agent:
            mock_agent_instance = Mock()
            mock_agent_instance.chat.return_value = "No work needed"
            mock_main_agent.return_value = mock_agent_instance
            # Execute node
            node = PlanningNode()
            next_node = await node.execute_async(context)
            # Verify transition to CompilingNode
            self.assertIsInstance(next_node, CompilingNode)
class TestExecutingNode(unittest.IsolatedAsyncioTestCase):
    """Test ExecutingNode."""
    def setUp(self):
        self.mock_task_store = Mock()
        self.mock_agent_store = Mock()
        self.mock_broadcast = Mock()
    async def test_executing_increments_wave_and_executes(self):
        """Test that executing node increments wave counter and executes subtasks."""
        context = StateContext(
            task_id="test-task-123",
            task_store=self.mock_task_store,
            agent_store=self.mock_agent_store,
            wave_count=0,
        )
        # Mock no ready subtasks (simplest case)
        self.mock_task_store.get_ready_subtasks.return_value = []
        # Execute node
        node = ExecutingNode()
        next_node = await node.execute_async(context)
        # Verify wave incremented
        self.assertEqual(context.wave_count, 1)
        # Verify transition to ValidatingNode
        self.assertIsInstance(next_node, ValidatingNode)
    async def test_executing_enforces_max_waves(self):
        """Test that exceeding max_waves raises RuntimeError."""
        context = StateContext(
            task_id="test-task-123",
            task_store=self.mock_task_store,
            agent_store=self.mock_agent_store,
            wave_count=19,
            max_waves=20,
        )
        node = ExecutingNode()
        with self.assertRaises(RuntimeError) as cm:
            await node.execute_async(context)
        self.assertIn("exceeded maximum wave limit", str(cm.exception))
class TestValidatingNode(unittest.IsolatedAsyncioTestCase):
    """Test ValidatingNode."""
    def setUp(self):
        self.mock_task_store = Mock()
        self.mock_agent_store = Mock()
        self.mock_broadcast = Mock()
    async def test_validating_more_work_loops_to_planning(self):
        """Test that if Maestro spawns more work, we loop back to PLANNING."""
        context = StateContext(
            task_id="test-task-123",
            task_store=self.mock_task_store,
            agent_store=self.mock_agent_store,
            wave_count=1,
        )
        # Track call count to simulate new subtask being spawned
        call_count = [0]
        def get_subtasks_side_effect(task_id):
            call_count[0] += 1
            base_subtasks = [{
                "id": "sub1",
                "status": "completed",
                "agent_id": "worker",
                "position": 0,
                "goal": "Work 1",
                "depends_on": [],
            }, {
                "id": "sub2",
                "status": "completed",
                "agent_id": "worker",
                "position": 1,
                "goal": "Work 2",
                "depends_on": [],
            }]
            if call_count[0] >= 4:  # After maestro spawns new work
                base_subtasks.append({
                    "id": "sub3",
                    "status": "pending",
                    "agent_id": "worker",
                    "position": 2,
                    "goal": "Work 3",
                    "depends_on": [],
                })
            return base_subtasks
        self.mock_task_store.get_subtasks_for_task.side_effect = get_subtasks_side_effect
        self.mock_task_store.get_task.return_value = {
            "id": "test-task-123",
            "goal": "Test goal",
            "working_directory": "/tmp/test",
        }
        self.mock_task_store.get_all_context.return_value = {}
        # Mock agent store
        self.mock_agent_store.get.return_value = {
            "system_prompt": "You are Maestro. {{AGENT_ROSTER}}",
        }
        self.mock_agent_store.list.return_value = []
        # Mock MainAgent
        with patch('agents.main_agent.MainAgent') as mock_main_agent:
            mock_agent_instance = Mock()
            mock_agent_instance.chat.return_value = "Need more work"
            # Mock get_message_history to return tool call with INCOMPLETE verdict
            mock_agent_instance.get_message_history.return_value = [
                {"role": "tool", "content": '{"verdict": "INCOMPLETE"}'}
            ]
            mock_main_agent.return_value = mock_agent_instance
            # Execute node
            node = ValidatingNode()
            next_node = await node.execute_async(context)
            # Verify transition to PlanningNode
            self.assertIsInstance(next_node, PlanningNode)
    async def test_validating_no_more_work_transitions_to_compiling(self):
        """Test that if Maestro says we're done, we transition to COMPILING."""
        context = StateContext(
            task_id="test-task-123",
            task_store=self.mock_task_store,
            agent_store=self.mock_agent_store,
            wave_count=2,
        )
        # Mock no failures or pending - same subtasks throughout (no new work)
        self.mock_task_store.get_subtasks_for_task.return_value = [{
            "id": "sub1",
            "status": "completed",
            "agent_id": "worker",
            "position": 0,
            "goal": "Work 1",
            "depends_on": [],
        }]
        self.mock_task_store.get_task.return_value = {
            "id": "test-task-123",
            "goal": "Test goal",
            "working_directory": "/tmp/test",
        }
        self.mock_task_store.get_all_context.return_value = {}
        # Mock agent store
        self.mock_agent_store.get.return_value = {
            "system_prompt": "You are Maestro. {{AGENT_ROSTER}}",
        }
        self.mock_agent_store.list.return_value = []
        # Mock MainAgent
        with patch('agents.main_agent.MainAgent') as mock_main_agent:
            mock_agent_instance = Mock()
            mock_agent_instance.chat.return_value = "Work complete"
            # Mock get_message_history to return tool call with COMPLETE verdict
            mock_agent_instance.get_message_history.return_value = [
                {"role": "tool", "content": '{"verdict": "COMPLETE"}'}
            ]
            mock_main_agent.return_value = mock_agent_instance
            # Execute node
            node = ValidatingNode()
            next_node = await node.execute_async(context)
            # Verify transition to CompilingNode
            self.assertIsInstance(next_node, CompilingNode)
class TestCompilingNode(unittest.IsolatedAsyncioTestCase):
    """Test CompilingNode."""
    async def test_compiling_runs_synthesis_and_transitions_to_finished(self):
        """Test that compiling runs synthesis and transitions to FINISHED."""
        mock_task_store = Mock()
        mock_agent_store = Mock()
        context = StateContext(
            task_id="test-task-123",
            task_store=mock_task_store,
            agent_store=mock_agent_store,
        )
        # Mock agent store
        mock_agent_store.exists.return_value = True
        mock_task_store.get_task.return_value = {
            "id": "test-task-123",
            "goal": "Test goal",
        }
        mock_task_store.get_subtasks_for_task.return_value = [
            {
                "id": "sub1",
                "status": "completed",
                "agent_id": "worker",
                "goal": "Do work",
                "output": "Work done",
            }
        ]
        # Mock MainAgent (summarizer)
        with patch('agents.main_agent.MainAgent') as mock_main_agent:
            mock_agent_instance = Mock()
            mock_agent_instance.chat.return_value = '{"summary": "Final output summary", "artifacts": []}'
            mock_main_agent.return_value = mock_agent_instance
            # Execute node
            node = CompilingNode()
            next_node = await node.execute_async(context)
            # Verify synthesis was called
            mock_agent_instance.chat.assert_called_once()
            # Verify final_output stored in context
            self.assertEqual(context.final_output, "Final output summary")
            # Verify transition to FinishedNode
            self.assertIsInstance(next_node, FinishedNode)
class TestFinishedNode(unittest.IsolatedAsyncioTestCase):
    """Test FinishedNode."""
    async def test_finished_node_raises_error_if_executed(self):
        """Test that FinishedNode raises error if execute_async is called."""
        context = StateContext(
            task_id="test",
            task_store=Mock(),
            agent_store=Mock(),
        )
        node = FinishedNode()
        with self.assertRaises(RuntimeError):
            await node.execute_async(context)
if __name__ == '__main__':
    unittest.main()
