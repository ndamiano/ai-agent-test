"""Worldgen game pipeline: draft a design SPEC, generate a village (world.ts) with worldgen, author
`main.ts` ON TOP of it through the real build loop (robust fix-classes), skin it, stage it.

  python -m maestro.codegen.worldgen_pipeline "<request>"

world.ts is PRE-PROVIDED (not planned/authored): a forced manifest names only main.ts, so the module
authors the gameplay against world.ts's exports (shown to it as sibling signatures) and never rebuilds
the town. The planner is bypassed on purpose — worldgen owns layout, the model owns gameplay."""
import json
import logging
import sys

from maestro.codegen import worldgen_bridge
from maestro.codegen.gates import game_dir, stage_for_play
from maestro.codegen.run import create_run, draft_spec, run_build
from maestro.state import RunState

logger = logging.getLogger(__name__)

VILLAGE_RECIPE = {
    "archetype": "continent", "size": "small",
    "palette": {"biomes": ["ocean", "beach", "grassland", "forest", "hill", "mountain"]},
    "locations": [{"id": "rivervale", "type": "settlement", "name": "Rivervale"}],
}

MAIN_PURPOSE = (
    "The WHOLE game, authored ON the PROVIDED worldgen village. Import { WORLD, spawnWorld, heightAt } "
    "from \"./world.ts\" (it already exists — the terrain, streets, and buildings; DO NOT author it or "
    "build your own town). config: { mode:\"3d\", controls:\"orbital\", background:\"#a9c7e0\", seed:1 } "
    "and NO camera hook. init(kit): call spawnWorld(this.state.world); create the player as a "
    "shape-tagged world entity at the plaza on the ground — "
    "this.state.player = kit.spawn(this.state.world, {shape:\"box\", x:WORLD.plaza.x, "
    "y:heightAt(WORLD.plaza.x,WORLD.plaza.z)+0.9, z:WORLD.plaza.z, w:0.8,h:1.7,d:0.8, color:\"#28303a\"}); "
    "then REALIZE THE SPEC'S entities on the village — place each NPC a couple units in front of a "
    "WORLD.buildings entry (type:\"npc\", mesh:\"npc\", a name + a line of dialogue), and scatter any "
    "collectible items on WORLD.grass cells, always with y = heightAt(x,z)+halfHeight. update(dt,input,kit): "
    "kit.drive(this.state.player,input,dt,9); keep the player on the ground every frame "
    "(this.state.player.y = heightAt(player.x,player.z)+0.9); interaction: when near an NPC and "
    "input.pressed(\"e\") open a talk state; render dialogue as a {kind:\"panel\"} + choices as "
    "{kind:\"menu\"} and act on kit.menuPick(input); complete the spec's objective with kit.win. Keep ALL "
    "state in this.state; randomness only via kit.rng. hud(kit): return Kit.HudItem[] (progress text + "
    "the talk panel/menu while talking)."
)


def build_worldgen_game(request: str, max_steps: int = 55):
    rid = create_run("pilot")
    st = RunState.for_run(rid)
    logger.info("worldgen game %s: drafting design for %r", rid, request)
    spec = draft_spec(request)
    spec["mode"] = "3d"
    spec["frozen"] = True
    st.write_spec(spec)

    gd = game_dir(st.run_dir)
    gd.mkdir(parents=True, exist_ok=True)
    info = worldgen_bridge.build(VILLAGE_RECIPE, gd)   # writes world.ts (+ a preview png)
    logger.info("worldgen game %s: village %sx%s, %d buildings",
                rid, info["gw"], info["gh"], len(info["buildings"]))

    manifest = {"files": [{"name": "main.ts", "purpose": MAIN_PURPOSE, "exports": ["createGame"]}]}
    (gd / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")

    result = run_build(rid, max_steps=max_steps)
    logger.info("worldgen game %s: build ok=%s steps=%s", rid, result.ok, result.steps)
    if result.ok:
        stage_for_play(st.run_dir, rid)
    return rid, result


def _cli(request: str) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s:%(name)s: %(message)s")
    rid, result = build_worldgen_game(request)
    print(f"\nRUN {rid}  ok={result.ok}  steps={result.steps}")
    if result.ok:
        print(f"play: runtime/index.html?game={rid}")
        print(f"skin: python -m maestro.codegen.run --assets {rid}")
    else:
        for e in result.failures:
            print(f"  unmet: [{e.component}] {e.code}: {str(e.message)[:160]}")
    return 0 if result.ok else 1


if __name__ == "__main__":
    if len(sys.argv) < 2:
        sys.exit('usage: python -m maestro.codegen.worldgen_pipeline "<request>"')
    sys.exit(_cli(" ".join(sys.argv[1:])))
