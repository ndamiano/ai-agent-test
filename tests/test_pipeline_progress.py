from unittest.mock import patch

from pipelines.registry import PipelineDefinition, run_subpipeline
from pipelines.runner import FnStage, Node, Pipeline, PipelineRunner
from tools.execution_context import execution_context, get_pipeline_path, get_task_id, pipeline_context


def test_pipeline_runner_emits_progress_events(tmp_path):
    def stage_fn(inputs, working_dir):
        return {"ok": True}

    pipeline = Pipeline(
        name="demo",
        nodes=[Node(id="make", stages=[FnStage(id="write", fn=stage_fn, inputs=[], output="out.json")])],
        prompts_dir=tmp_path,
    )

    with execution_context(task_id="task-1", subtask_id="sub-1"):
        with patch("api.websocket.event_bus.event_bus.publish_sync") as publish:
            assert PipelineRunner(str(tmp_path / "run")).run(pipeline, {"goal": "test"})

    event_types = [call.args[0]["type"] for call in publish.call_args_list]
    assert event_types == [
        "pipeline_started",
        "pipeline_node_started",
        "pipeline_stage_started",
        "pipeline_stage_completed",
        "pipeline_node_completed",
        "pipeline_completed",
    ]
    first_event = publish.call_args_list[0].args[0]
    assert first_event["task_id"] == "task-1"
    assert first_event["subtask_id"] == "sub-1"
    assert first_event["pipeline"] == "demo"


def test_run_queued_pipelines_preserves_execution_context(monkeypatch):
    from tools import pipeline_tools

    seen_task_ids = []
    seen_pipeline_paths = []

    def fake_run(name, brief, working_dir):
        seen_task_ids.append(get_task_id())
        seen_pipeline_paths.append(get_pipeline_path())
        return {"result": {"ok": True}}

    monkeypatch.setattr("pipelines.registry.run_pipeline", fake_run)

    with execution_context(task_id="task-queued", subtask_id="sub-queued"):
        with pipeline_context(["parent"]):
            pipeline_tools.queue_pipeline("character", {"concept": "rogue"})
            pipeline_tools.run_queued_pipelines()

    assert seen_task_ids == ["task-queued"]
    assert seen_pipeline_paths == [["parent"]]


def test_subpipeline_returns_outputs_and_emits_nested_path(tmp_path, monkeypatch):
    def child_fn(inputs, working_dir):
        return {"value": "from child"}

    child = Pipeline(
        name="child",
        nodes=[Node(id="child_node", stages=[FnStage(id="child_stage", fn=child_fn, inputs=[], output="child.json")])],
        prompts_dir=tmp_path,
    )

    def parent_fn(inputs, working_dir):
        result = run_subpipeline(working_dir, "child", {"kind": "nested"}, run_id="child-run")
        return {"child_status": result["status"], "child_value": result["outputs"]["child"]["value"]}

    parent = Pipeline(
        name="parent",
        nodes=[Node(id="parent_node", stages=[FnStage(id="parent_stage", fn=parent_fn, inputs=[], output="parent.json")])],
        prompts_dir=tmp_path,
    )

    monkeypatch.setattr(
        "pipelines.registry._registry",
        {
            "child": PipelineDefinition(
                name="child",
                description="test child",
                pipeline=child,
                brief_schema={"required": []},
            )
        },
    )

    with execution_context(task_id="task-nested", subtask_id="sub-nested"):
        with patch("api.websocket.event_bus.event_bus.publish_sync") as publish:
            assert PipelineRunner(str(tmp_path / "parent-run")).run(parent, {"kind": "parent"})

    nested_events = [call.args[0] for call in publish.call_args_list if call.args[0]["pipeline"] == "child"]
    assert nested_events
    assert all(event["pipeline_path"] == ["parent", "child"] for event in nested_events)

    parent_output = tmp_path / "parent-run" / "parent.json"
    assert parent_output.exists()
    assert '"child_value": "from child"' in parent_output.read_text()
