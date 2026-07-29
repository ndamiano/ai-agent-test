import contextvars

from tools.execution_context import (
    execution_context,
    get_execution_context,
    get_subtask_id,
    get_task_id,
)


class TestExecutionContext:
    def test_context_manager_basic(self):
        assert get_task_id() is None
        assert get_subtask_id() is None

        with execution_context(task_id="test-123", subtask_id="sub-456"):
            assert get_task_id() == "test-123"
            assert get_subtask_id() == "sub-456"
            assert get_execution_context()['task_id'] is not None

        assert get_task_id() is None
        assert get_subtask_id() is None
        assert get_execution_context()['task_id'] is None

    def test_partial_context(self):
        with execution_context(task_id="test-task"):
            assert get_task_id() == "test-task"
            assert get_subtask_id() is None

        with execution_context(subtask_id="test-sub"):
            assert get_task_id() is None
            assert get_subtask_id() == "test-sub"

    def test_get_execution_context(self):
        context = get_execution_context()
        assert context == {'task_id': None, 'subtask_id': None, 'working_directory': None}

        with execution_context(task_id="t1", subtask_id="s1"):
            context = get_execution_context()
            assert context['task_id'] == 't1'
            assert context['subtask_id'] == 's1'
