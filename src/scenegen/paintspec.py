"""One call: the LLM art-directs each terrain region's guide color and material phrase.

The guide color ANCHORS the final hue — at the denoise the ground pass runs, no prompt
wording overrides a garish flat color (measured: traffic-cone sand survived every prompt
until the guide hex changed). So color quality is decided here, before any diffusion.
The layout's own tileset hex rides along as the region's intent: a bright yellow treasury
must stay gold, not become tasteful slate.
"""
from __future__ import annotations

import re
from typing import Dict, List

from scenegen.layout import Llm, _prompt, chat_json

_HEX = re.compile(r"#[0-9a-fA-F]{6}")


def paint_spec(llm: Llm, place: str, terrain: List[Dict]) -> Dict[str, Dict]:
    """{terrain name: {"color": "#hex", "phrase": str}} for every terrain."""
    names = [t["name"] for t in terrain]
    listing = "\n".join(f"- {t['name']}: {t['color']}" for t in terrain)

    def validate(obj):
        ts = obj.get("terrains")
        if not isinstance(ts, list):
            return "reply needs a terrains list"
        byname = {d.get("name"): d for d in ts if isinstance(d, dict)}
        missing = [n for n in names if n not in byname]
        if missing:
            return f"missing terrains: {', '.join(missing)} — write every region"
        for d in byname.values():
            if not (isinstance(d.get("color"), str) and _HEX.fullmatch(d["color"].strip())):
                return f"color for '{d.get('name')}' must be a hex like #8a9b6c"
            if not (isinstance(d.get("phrase"), str) and d["phrase"].strip()):
                return f"phrase for '{d.get('name')}' must be a short material description"
        return None

    obj = chat_json(llm, _prompt("paintspec", place=place, terrains=listing), validate)
    return {d["name"]: {"color": d["color"].strip(), "phrase": d["phrase"].strip()}
            for d in obj["terrains"]}


def fallback_spec(terrain: List[Dict]) -> Dict[str, Dict]:
    """A spec from the tileset alone, for when the paintspec call is refused or dies —
    the ground still paints, just with the layout's own colors and names as phrases."""
    return {t["name"]: {"color": t["color"], "phrase": t["name"].replace("_", " ")}
            for t in terrain}
