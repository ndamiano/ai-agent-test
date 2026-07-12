import sys
from pathlib import Path

# tests/ is on sys.path and its `worldgen` test package shadows src/worldgen as an importable
# package — force src/ ahead so the tool's `import worldgen` resolves to the real one.
_SRC = str(Path(__file__).parent.parent / "src")
if _SRC in sys.path:
    sys.path.remove(_SRC)
sys.path.insert(0, _SRC)

from maestro.state import RunState
from maestro.tools import build_tools
from maestro.modules.world import MODULE as WORLD, v_places, _d_world_gen, _d_min_places
from conftest import make_spec

_RECIPE = dict(
    archetype="archipelago",
    size="small",
    palette={"biomes": ["tropical shallows", "beach", "jungle", "rocky highlands"]},
    locations=[
        {"id": "home_port", "type": "settlement", "want": "coastal harbor"},
        {"id": "tavern", "type": "interior", "host": "home_port"},
        {"id": "wilds", "type": "wilderness", "want": "jungle inland"},
    ],
)


def _gen(tmp_path):
    state = RunState(tmp_path)
    tools = build_tools(make_spec(), state)
    res = tools["generate_world"](**_RECIPE)
    return state, res


def test_generate_world_writes_valid_places(tmp_path):
    state, res = _gen(tmp_path)
    assert res["ok"], res
    places = state.read_component("places")
    assert v_places(places) is None
    assert places["generated"]["recipe"]["archetype"] == "archipelago"
    assert places["start_place"] == "home_port"
    assert "home_port" in places["place_ids"]
    assert "tavern" in places["place_ids"]


def test_generate_world_rejects_bad_recipe(tmp_path):
    state = RunState(tmp_path)
    tools = build_tools(make_spec(), state)
    res = tools["generate_world"](archetype="donut", size="small",
                                  palette={"biomes": ["x"]}, locations=[])
    assert not res["ok"]
    assert "recipe" in res["error"]


class _Ctx:
    def __init__(self, artifact, spec):
        self.artifact = artifact
        self.spec = spec

    def param(self, key, default):
        return default


def _check(code):
    return next(c for c in WORLD.checks if c.code == code)


def test_world_gen_check_fires_then_clears(tmp_path):
    chk = _check("world_gen")
    ctx = _Ctx({}, {"modules": ["combat"]})
    assert _d_world_gen(chk, WORLD, ctx)

    state, _ = _gen(tmp_path)
    ctx2 = _Ctx({"places": state.read_component("places")}, {"modules": ["combat"]})
    assert _d_world_gen(chk, WORLD, ctx2) == []

    pnc = _Ctx({}, {"modules": []})
    assert _d_world_gen(chk, WORLD, pnc) == []


def test_min_places_and_furniture_gated_for_generated(tmp_path):
    state, _ = _gen(tmp_path)
    art = {"places": state.read_component("places")}
    ctx = _Ctx(art, {"modules": ["combat"]})
    assert _d_min_places(_check("min_places"), WORLD, ctx) == []
    from maestro.modules.world import _d_furniture
    assert _d_furniture(_check("furniture"), WORLD, ctx) == []


def test_add_interactable_auto_places_on_generated_zone(tmp_path):
    state, _ = _gen(tmp_path)
    places = state.read_component("places")
    pid = next(p for p in places["place_ids"]
               if places["places"][p]["kind"] == "world_map")
    tools = build_tools(make_spec(), state)
    res = tools["add_interactable"](pid, {
        "id": "h_dig", "label": "dig here",
        "action": {"type": "examine", "text": "Soft sand."}})
    assert res.get("ok"), res
    places = state.read_component("places")
    hot = next(h for h in places["places"][pid]["interactables"] if h["id"] == "h_dig")
    cell = hot["position"]["cell"]
    rows = places["places"][pid]["tiles"]["rows"]
    legend = places["places"][pid]["tiles"]["legend"]
    assert legend[rows[cell["y"]][cell["x"]]]["role"] == "open"


def test_add_interactable_named_feature_falls_through_when_no_anchors(tmp_path):
    state, _ = _gen(tmp_path)
    places = state.read_component("places")
    pid = next(p for p in places["place_ids"]
               if not (places["places"][p].get("anchors") or {}))
    tools = build_tools(make_spec(), state)
    res = tools["add_interactable"](pid, {
        "id": "h_stack", "label": "salt stack",
        "position": {"feature": "f_center"},
        "action": {"type": "examine", "text": "Bricks of grey salt."}})
    assert res.get("ok"), res
    places = state.read_component("places")
    hot = next(h for h in places["places"][pid]["interactables"] if h["id"] == "h_stack")
    cell = hot["position"]["cell"]
    rows = places["places"][pid]["tiles"]["rows"]
    legend = places["places"][pid]["tiles"]["legend"]
    assert legend[rows[cell["y"]][cell["x"]]]["role"] == "open"
