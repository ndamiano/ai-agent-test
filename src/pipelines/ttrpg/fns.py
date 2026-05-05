import json
from pathlib import Path
from typing import Dict

from pipelines.runner import render_template
from llm_clients.inference import PipelineAgent, strip_fences

_PROMPTS_DIR = Path(__file__).parent / "prompts"

_SYSTEM = (
    "You are a precise creative writing assistant. Output only valid JSON. "
    "No markdown, no explanation, no code fences."
)


def _json_with_correction(agent: PipelineAgent, prompt: str, label: str) -> dict:
    content = strip_fences(agent.send(prompt))
    for _ in range(2):
        try:
            return json.loads(content)
        except json.JSONDecodeError:
            content = strip_fences(agent.send(
                "Invalid JSON. Return only the JSON object, no other text."
            ))
    raise RuntimeError(f"Failed to get valid JSON for {label}")


def generate_npcs(inputs: Dict, working_dir: Path) -> Dict:
    brief = inputs.get("brief", {})

    setting = inputs.get("setting", {})
    if isinstance(setting, dict) and "name" not in setting:
        setting = setting.get("setting", {})

    factions = inputs.get("factions", [])
    if isinstance(factions, dict):
        factions = factions.get("factions", [])

    total = int(brief.get("npc_count", 5))
    faction_summaries = [
        {"id": f["id"], "name": f["name"], "goal": f.get("goal", "")}
        for f in factions
    ]

    npcs = []
    for i in range(total):
        label = f"{i + 1}/{total}"
        print(f"    [npcs]  generating NPC {label}")

        ctx = {
            **brief,
            "index":          i + 1,
            "total":          total,
            "overview":       setting.get("overview", ""),
            "faction_summaries": faction_summaries,
            "existing_npcs":  [
                {"id": n["id"], "name": n["name"], "role": n.get("role", ""), "faction_id": n.get("faction_id")}
                for n in npcs
            ],
        }
        prompt = render_template(_PROMPTS_DIR / "npc.txt", ctx)
        agent = PipelineAgent(_SYSTEM)
        npc = _json_with_correction(agent, prompt, f"NPC {label}")
        npcs.append(npc)

    return {"npcs": npcs}


def assemble_document(inputs: Dict, working_dir: Path) -> Dict:
    brief    = inputs.get("brief", {})
    setting  = inputs.get("setting", {})
    if isinstance(setting, dict) and "name" not in setting:
        setting = setting.get("setting", {})

    factions = inputs.get("factions", [])
    if isinstance(factions, dict):
        factions = factions.get("factions", [])

    npcs = inputs.get("npcs", [])
    if isinstance(npcs, dict):
        npcs = npcs.get("npcs", [])

    encounters = inputs.get("encounters", [])
    if isinstance(encounters, dict):
        encounters = encounters.get("encounters", [])

    plot = inputs.get("plot_hooks", {})

    title = brief.get("title", setting.get("name", "Campaign"))
    lines = [f"# {title}", ""]

    # World
    lines += ["## The World", "", setting.get("overview", ""), ""]
    if setting.get("magic_tech_level"):
        lines += [f"**Power & Technology:** {setting['magic_tech_level']}", ""]
    tensions = setting.get("key_tensions", [])
    if tensions:
        lines += ["**Key Tensions:**"] + [f"- {t}" for t in tensions] + [""]
    loc = setting.get("starting_location", {})
    if loc:
        lines += [f"### Starting Location: {loc.get('name', '')}", "", loc.get("description", ""), ""]
        hooks = loc.get("hooks", [])
        if hooks:
            lines += ["**Arrival Hooks:**"] + [f"- {h}" for h in hooks] + [""]

    # Factions
    if factions:
        lines += ["## Factions", ""]
        for f in factions:
            rel = f.get("relationship_to_players", "neutral").title()
            lines += [
                f"### {f['name']}",
                f"*{rel}*", "",
                f"**Goal:** {f.get('goal', '')}",
                f"**Method:** {f.get('method', '')}",
                f"**Strength:** {f.get('strength', '')}",
                f"**Weakness:** {f.get('weakness', '')}",
                "",
            ]

    # NPCs
    if npcs:
        lines += ["## Notable NPCs", ""]
        for n in npcs:
            lines += [
                f"### {n['name']}",
                f"*{n.get('role', '')}*", "",
                n.get("description", ""),
                f"**Motivation:** {n.get('motivation', '')}",
                f"**Secret:** {n.get('secret', '')}",
                f"**Opening:** \"{n.get('dialogue_hook', '')}\"",
                "",
            ]

    # Encounters
    if encounters:
        lines += ["## Encounters", ""]
        for e in encounters:
            enc_type = e.get("type", "").title()
            lines += [
                f"### {e['title']} *({enc_type})*", "",
                e.get("setup", ""),
                f"**Stakes:** {e.get('stakes', '')}",
                f"**Twist:** {e.get('twist', '')}",
                "",
            ]

    # Main quest
    mq = plot.get("main_quest", {})
    if mq:
        lines += ["## Main Quest", f"### {mq.get('title', '')}", ""]
        lines += [
            f"**Hook:** {mq.get('inciting_incident', '')}",
            f"**Goal:** {mq.get('goal', '')}",
            f"**Stakes:** {mq.get('stakes', '')}",
        ]
        obstacles = mq.get("obstacles", [])
        if obstacles:
            lines += ["**Obstacles:**"] + [f"- {o}" for o in obstacles]
        lines += [f"**Climax:** {mq.get('climax', '')}", ""]

    # Side quests
    side = plot.get("side_quests", [])
    if side:
        lines += ["## Side Quests", ""]
        for sq in side:
            lines += [
                f"### {sq['title']}", "",
                f"**Hook:** {sq.get('hook', '')}",
                f"**Goal:** {sq.get('goal', '')}",
                f"**Reward:** {sq.get('reward', '')}",
                "",
            ]

    # Random hooks
    random_hooks = plot.get("random_hooks", [])
    if random_hooks:
        lines += ["## Random Hooks", ""] + [f"- {h}" for h in random_hooks] + [""]

    doc = "\n".join(lines)
    doc_path = working_dir / "campaign.md"
    doc_path.write_text(doc, encoding="utf-8")

    print(f"    [document]  wrote {doc_path.name} ({len(doc)} chars)")
    return {"status": "ok", "file": str(doc_path), "title": title}
