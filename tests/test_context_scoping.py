"""Context-scoping fixes from the live-run audit (quality_todo §13): per-mode skeletons, trimmed
upstream, scoped SPEC block, the per-node location gate, and the relaxed VN cast floor."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from maestro.spec import Spec
from maestro.state import RunState
from maestro.tools import build_tools
from maestro.executor import Executor
from maestro.validate import run_check
from renpy.component_schemas import SCHEMAS
from renpy.ir_checks import register_all

register_all()


# ── Fix 1: per-mode skeleton guide ────────────────────────────────────────────
class _CaptureConn:
    def __init__(self):
        self.systems = []

    def generate_with_tools(self, messages, schemas, **kw):
        self.systems.append(next(m["content"] for m in messages if m["role"] == "system"))
        return {"choices": [{"message": {"tool_calls": []}}]}


def test_mode_guides_scope_skeleton_to_active_component():
    from maestro.agent import make_llm_decider
    conn = _CaptureConn()
    decider = make_llm_decider(connector=conn,
                               mode_guides={"premise": "PREMISE_SHAPE", "nodes": "NODES_SHAPE"},
                               mode_prompts={})
    decider({"mode": "nodes", "spec": {"components": []}, "todo": []})
    sysmsg = conn.systems[-1]
    assert "NODES_SHAPE" in sysmsg
    assert "PREMISE_SHAPE" not in sysmsg          # other components' skeletons are NOT carried


# ── Fix 2: trimmed upstream (asset_manifest → ids) ────────────────────────────
def test_asset_manifest_context_view_is_ids_only():
    from maestro.discrete.assets import _asset_manifest_view
    full = {"backgrounds": [{"id": "bg_room", "image_file": "room.png", "description": "a long SD prompt"}],
            "characters": [{"id": "ada", "image_file": "ada.png", "description": "hair, eyes, ..."}],
            "cgs": [{"id": "cg_end", "description": "x"}]}
    v = _asset_manifest_view(full)
    assert v == {"backgrounds": ["bg_room"], "characters": ["ada"]}
    assert "description" not in str(v) and "image_file" not in str(v)


def test_compose_collects_context_views():
    from maestro.modules import compose, PRESETS
    c = compose(PRESETS["vn"].modules)
    assert "asset_manifest" in c.context_views
    assert "premise" not in c.context_views        # premise is left whole (load-bearing for voice)


def test_executor_injects_trimmed_upstream(tmp_path):
    from maestro.discrete.assets import _asset_manifest_view
    state = RunState(tmp_path)
    state.write_component("premise", {"characters": [{"id": "ada", "name": "Ada"}]})
    state.write_component("asset_manifest", {"backgrounds": [
        {"id": "bg_room", "image_file": "room.png", "description": "a 300-char Stable Diffusion prompt"}],
        "characters": [{"id": "ada", "image_file": "ada.png", "description": "appearance prose"}]})
    spec = Spec({"frozen": True, "components": [
        {"id": "premise", "done_conditions": [{"type": "exists", "path": "premise.characters"}]},
        {"id": "asset_manifest", "done_conditions": [{"type": "exists", "path": "asset_manifest.backgrounds"}]},
        {"id": "nodes", "done_conditions": [{"type": "count", "path": "nodes.node_ids", "min": 1}]}]})
    ex = Executor(spec, state, {}, lambda c: {}, upstream_views={"asset_manifest": _asset_manifest_view})
    ctx = ex.build_context()
    assert ctx["upstream"]["asset_manifest"] == {"backgrounds": ["bg_room"], "characters": ["ada"]}
    assert "Stable Diffusion" not in str(ctx["upstream"])     # prose dropped


# ── Fix 3: scoped SPEC block ──────────────────────────────────────────────────
def test_scoped_spec_drops_inactive_done_conditions():
    from maestro.agent import _scoped_spec
    ctx = {"mode": "nodes", "spec": {"title": "T", "components": [
        {"id": "premise", "description": "cast", "done_conditions": [{"type": "count", "min": 3}]},
        {"id": "nodes", "description": "scenes", "done_conditions": [{"type": "count", "min": 20}]}]}}
    out = _scoped_spec(ctx)
    by = {c["id"]: c for c in out["components"]}
    assert "done_conditions" in by["nodes"]                   # active: full
    assert "done_conditions" not in by["premise"]             # inactive: id+description only
    assert by["premise"]["description"] == "cast"


# ── Fix 6: per-node location gate + edit_node location ────────────────────────
def test_each_node_has_location_check_and_edit_fix(tmp_path):
    state = RunState(tmp_path)
    state.write_component("nodes", {"node_ids": ["s1", "s2"], "nodes": {
        "s1": {"location": "bg_a", "lines": [{"speaker": None, "text": "x"}], "end": {"type": "return"}},
        "s2": {"lines": [{"speaker": None, "text": "y"}], "end": {"type": "return"}}}})  # s2 no location
    chk = {"type": "each_node_has_location"}
    ok, detail, _ = run_check(chk, state.load_artifact(), state.run_dir)
    assert ok is False and "s2" in detail

    spec = Spec({"frozen": True, "components": [{"id": "nodes", "done_conditions": []}]})
    tools = build_tools(spec, state, schemas=SCHEMAS)
    assert tools["edit_node"](node_id="s2", location="bg_b")["ok"] is True
    ok2, _, _ = run_check(chk, state.load_artifact(), state.run_dir)
    assert ok2 is True


# ── Fix 4: relaxed VN cast floor ──────────────────────────────────────────────
def test_vn_premise_character_floor_is_two():
    from maestro.modules import compose, PRESETS
    base = compose(PRESETS["vn"].modules).baseline["premise"]
    cnt = next(c for c in base if c.get("type") == "count" and c.get("path") == "premise.characters")
    assert cnt["min"] == 2
