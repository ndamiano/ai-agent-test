"""Compile a Game IR (genre: point_and_click) into a Ren'Py script.rpy.

Parallel to ir_vn. A `place` becomes: one clickable `screen` (coordinate-placed hotspot buttons
over the background), a persistent inventory bar, and a driver `label` that loops the screen.
Each interactable's structured `action` (examine/take/talk/move/use/win/start_combat) is projected
into a generated `label hs_<pid>_<hid>:` the screen `Call`s — so the agent never writes Ren'Py.
NPC dialogue reuses the IR nodes via ir_vn.node_block (talk hotspots `call` them).

Pure: IR dict in, script string out. Reference integrity is guaranteed upstream by ir_crossref.
"""

import json
from typing import Dict, List

from renpy.ir_vn import _IND, _condition, _effect, _num, node_block


def _inventory_screen() -> str:
    return (
        "screen inventory_bar():\n"
        "    zorder 100\n"
        "    frame:\n"
        "        align (0.5, 0.98)\n"
        "        has hbox\n"
        "        spacing 10\n"
        '        text "Inventory:" yalign 0.5\n'
        "        for _item_id in inventory:\n"
        "            add _item_id zoom 0.4\n"
    )


def _hs_label(pid: str, hid: str) -> str:
    return f"hs_{pid}_{hid}"


def _rect(pos: Dict):
    r = (pos or {}).get("rect") or {}
    return int(r.get("x", 0)), int(r.get("y", 0)), int(r.get("w", 100)), int(r.get("h", 100))


def _place_screen(place: Dict) -> str:
    pid = place["id"]
    lines = [f"screen {pid}():", "    tag room",
             f'    add "{place.get("background", "black")}"', "    use inventory_bar"]
    for h in place.get("interactables", []):
        x, y, w, ht = _rect(h.get("position"))
        label = h.get("label", h.get("id", ""))
        lines += [
            "    button:",
            f"        xpos {x} ypos {y}",
            f"        xysize ({w}, {ht})",
            f'        action Call("{_hs_label(pid, h.get("id", ""))}")',
            '        background "#ffffff18"',
            '        hover_background "#ffd70055"',
            f"        text {json.dumps(label)}:",
            "            size 16",
            "            align (0.5, 0.5)",
            '            outlines [(2, "#000", 0, 0)]',
        ]
    return "\n".join(lines) + "\n"


def _place_driver(place: Dict, music: Dict = None) -> str:
    from maestro.music import track_for_place, file_for_track
    pid = place["id"]
    mf = file_for_track(music, track_for_place(music, pid))
    music_line = f'    play music "audio/music/{mf}" fadeout 1.0 fadein 1.0 if_changed\n' if mf else ""
    return (
        f"label {pid}:\n"
        f'    $ current_room = "{pid}"\n'
        f"{music_line}"
        f'    scene {place.get("background", "black")}\n'
        f"label _loop_{pid}:\n"
        f"    call screen {pid}\n"
        f"    jump _loop_{pid}\n"
    )


def _outcome(outcome: Dict, ind: str) -> List[str]:
    out = []
    if outcome.get("text"):
        out.append(f"{ind}{json.dumps(outcome['text'])}")
    for eff in outcome.get("effects", []):
        out.append(f"{ind}{_effect(eff)}")
    return out or [f"{ind}pass"]


def _action_body(action: Dict) -> List[str]:
    """The body of a hotspot label — guaranteed to terminate via `return` or `jump`."""
    t = action.get("type")
    ind = _IND
    if t == "examine":
        return [f"{ind}{json.dumps(action['text'])}", f"{ind}return"]
    if t == "take":
        item = action["item"]
        out = [f"{ind}$ if {json.dumps(item)} not in inventory: inventory.append({json.dumps(item)})"]
        if action.get("text"):
            out.append(f"{ind}{json.dumps(action['text'])}")
        return out + [f"{ind}return"]
    if t == "talk":
        return [f"{ind}call {action['node']}", f"{ind}return"]
    if t == "move":
        # Hotspot labels are entered via the screen's Call(); a plain jump would leak that call
        # frame (growing the stack every move, and making a later `return` resume the old room),
        # so pop it before leaving.
        if action.get("requires"):
            return [f"{ind}if {_condition(action['requires'])}:",
                    f"{ind}{_IND}$ renpy.pop_call()",
                    f"{ind}{_IND}jump {action['target']}", f"{ind}return"]
        return [f"{ind}$ renpy.pop_call()", f"{ind}jump {action['target']}"]
    if t == "use":
        out = []
        clauses = action.get("clauses", [])
        for i, clause in enumerate(clauses):
            kw = "if" if i == 0 else "elif"
            out.append(f"{ind}{kw} {_condition(clause['requires'])}:")
            out += _outcome(clause.get("outcome", {}), ind + _IND)
        if action.get("fallback"):
            if clauses:
                out.append(f"{ind}else:")
                out += _outcome(action["fallback"], ind + _IND)
            else:
                # No clauses → the fallback is unconditional; emit it directly (no dangling else).
                out += _outcome(action["fallback"], ind)
        return out + [f"{ind}return"]
    if t == "win":
        # Same frame-pop as move: without it, `label win:`'s final `return` pops back into the
        # room loop and the game never ends. With the frame popped, that `return` ends the game.
        if action.get("requires"):
            return [f"{ind}if {_condition(action['requires'])}:",
                    f"{ind}{_IND}$ renpy.pop_call()",
                    f"{ind}{_IND}jump win", f"{ind}return"]
        return [f"{ind}$ renpy.pop_call()", f"{ind}jump win"]
    if t == "start_combat":
        # Combat has no Ren'Py projection yet; resolve as a no-op so the room stays playable.
        return [f"{ind}# start_combat: {action.get('encounter')} (combat backend pending)",
                f"{ind}return"]
    return [f"{ind}return"]


def _hotspot_block(pid: str, h: Dict) -> str:
    return f"label {_hs_label(pid, h.get('id', ''))}:\n" + "\n".join(_action_body(h["action"])) + "\n"


def compile_pnc(ir: Dict) -> str:
    """Return the Ren'Py script.rpy source for a point_and_click IR."""
    places = ir.get("places", [])
    out: List[str] = ["## Characters",
                      'define act = Character(None, what_italic=True, what_color="#a0a0a0")']
    for c in ir.get("characters", []):
        out.append(f"define {c['id']} = Character({json.dumps(c.get('name', c['id']))})")

    out.append("")
    out.append("## Images")
    bg_files = {bg["id"]: bg["image_file"] for bg in ir.get("backgrounds", [])}
    for bg in sorted({p.get("background") for p in places if p.get("background")}):
        fname = bg_files.get(bg, f"{bg}.png")
        out.append(f'image {bg} = "images/{fname}"')
    for it in ir.get("items", []):
        out.append(f'image {it["id"]} = "images/{it["id"]}.png"')

    out.append("")
    out.append("## State")
    out.append("default inventory = []")
    start = ir.get("start", {}).get("place") or (places[0]["id"] if places else "")
    out.append(f'default current_room = "{start}"')
    for f in ir.get("flags", []):
        out.append(f"default {f} = False")
    for v in ir.get("variables", []):
        out.append(f"default {v['id']} = {_num(v['default'])}")
    out.append("")

    out.append(_inventory_screen())
    for p in places:
        out.append(_place_screen(p))

    out += ["label splashscreen:", "    return", "",
            "label start:", f"    jump {start}", "",
            "label win:", "    scene black", '    centered "You won."', "    return", ""]

    music = ir.get("music")
    for p in places:
        out.append(_place_driver(p, music))
        for h in p.get("interactables", []):
            out.append(_hotspot_block(p["id"], h))

    for node in ir.get("nodes", []):
        out.extend(node_block(node))
        out.append("")

    return "\n".join(out).rstrip() + "\n"
