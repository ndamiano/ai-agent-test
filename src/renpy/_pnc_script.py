"""Stitch a point-and-click adventure into a Ren'Py script.rpy.

Parallel to renpy/_script.py (the VN stitch). A `rooms` artifact becomes: one clickable
`screen` per room (coordinate-placed hotspot buttons over the background — we use plain
`button`s with a Solid highlight rather than a baked imagemap, since we have no pre-drawn
hover art), a persistent inventory bar, and a driver `label` per room that loops the
screen. Each hotspot's agent-authored `logic` body is emitted under a generated label the
screen `Call`s, so the screen wiring can never drift from the logic. NPC dialogue reuses
the VN node_scripts (its labels are appended verbatim and `call`ed by talk hotspots).
"""

import re
import textwrap
import json
from typing import Dict, List, Set, Tuple

from renpy._script import _postprocess_script

_LABEL_LINE = re.compile(r'^\s*label\s+\w+\s*:\s*$')
_JUMP_RE = re.compile(r'\bjump\s+(\w+)')
_CALL_RE = re.compile(r'\bcall\s+(\w+)')
_MARKER_RE = re.compile(r'^\s*(?:label|screen)\s+(\w+)')


def _hs_label(rid: str, hid: str) -> str:
    return f"hs_{rid}_{hid}"


def pnc_line_ranges(full_script: str, room_ids: List[str],
                    node_ids: List[str]) -> List[Tuple[int, int, str]]:
    """Map each script.rpy line span to the room (or dialogue node) that owns it, so a PNC
    lint error on a line can be attributed back to the component the agent must fix —
    the rooms equivalent of node_line_ranges. A room owns several blocks: its `screen <rid>`,
    its driver `label <rid>`, its `label _loop_<rid>`, and one `label hs_<rid>_<hid>` per
    hotspot — all keyed back to <rid>. Node script labels map to the node id."""
    # Longest rid first so a room id that is a prefix of another (room_a vs room_a_b) does
    # not shadow it when matching the hs_<rid>_ hotspot labels.
    rids = sorted(room_ids, key=len, reverse=True)
    markers = []
    for i, ln in enumerate(full_script.split("\n"), 1):
        m = _MARKER_RE.match(ln)
        if not m:
            continue
        name = m.group(1)
        owner = next((rid for rid in rids
                      if name == rid or name == f"_loop_{rid}"
                      or name.startswith(f"hs_{rid}_")), None)
        if owner is None and name in node_ids:
            owner = name
        markers.append((i, owner))
    markers.sort()
    ranges = []
    for idx, (start, owner) in enumerate(markers):
        if owner is None:
            continue
        end = markers[idx + 1][0] - 1 if idx + 1 < len(markers) else 10 ** 9
        ranges.append((start, end, owner))
    return ranges


def _strip_leading_label(body: str) -> str:
    """Drop a leading `label X:` the agent may have wrapped its body in — we emit our own
    label so the screen's Call target always matches. Then dedent for re-indentation."""
    lines = body.split("\n")
    while lines and not lines[0].strip():
        lines.pop(0)
    if lines and _LABEL_LINE.match(lines[0]):
        lines = lines[1:]
    return textwrap.dedent("\n".join(lines)).strip("\n")


def _hotspot_logic_block(rid: str, hotspot: Dict) -> str:
    """`label hs_<rid>_<hid>:` + the agent's logic body (re-indented), guaranteed to
    terminate (a hotspot that neither returns nor jumps would hang the room loop)."""
    hid = hotspot.get("id", "")
    body = _strip_leading_label(hotspot.get("logic", "") or "")
    indented = textwrap.indent(body, "    ") if body else "    return"
    terminates = bool(_JUMP_RE.search(body)) or body.rstrip().endswith("return")
    tail = "" if terminates else "\n    return"
    return f"label {_hs_label(rid, hid)}:\n{indented}{tail}\n"


def _room_screen(rid: str, room: Dict) -> str:
    lines = [f"screen {rid}():", "    tag room", f'    add "{room.get("bg", "black")}"',
             "    use inventory_bar"]
    for h in room.get("hotspots", []):
        x, y, w, ht = (list(h.get("rect", [0, 0, 100, 100])) + [0, 0, 100, 100])[:4]
        label = h.get("label", h.get("id", ""))
        lines += [
            "    button:",
            f"        xpos {int(x)} ypos {int(y)}",
            f"        xysize ({int(w)}, {int(ht)})",
            f'        action Call("{_hs_label(rid, h.get("id", ""))}")',
            '        background "#ffffff18"',
            '        hover_background "#ffd70055"',
            f"        text {json.dumps(label)}:",
            "            size 16",
            "            align (0.5, 0.5)",
            '            outlines [(2, "#000", 0, 0)]',
        ]
    return "\n".join(lines) + "\n"


def _inventory_screen() -> str:
    return (
        "screen inventory_bar():\n"
        "    zorder 100\n"
        "    frame:\n"
        "        align (0.5, 0.98)\n"
        "        has hbox\n"
        "        spacing 10\n"
        "        text \"Inventory:\" yalign 0.5\n"
        "        for _item_id in inventory:\n"
        "            add _item_id zoom 0.4\n"
    )


def _room_driver(rid: str, room: Dict) -> str:
    return (
        f"label {rid}:\n"
        f'    $ current_room = "{rid}"\n'
        f'    scene {room.get("bg", "black")}\n'
        f"label _loop_{rid}:\n"
        f"    call screen {rid}\n"
        f"    jump _loop_{rid}\n"
    )


def _defines(premise: Dict, manifest: Dict) -> List[str]:
    """Character + image + transform defines (mirrors the VN stitch's header so reused
    NPC dialogue nodes render with the same staging)."""
    lines = ["## Characters",
             'define act = Character(None, what_italic=True, what_color="#a0a0a0")']
    for char in premise.get("characters", []):
        lines.append(f'define {char["id"]} = Character({json.dumps(char["name"])}, '
                     f'color="{char.get("color", "#ffffff")}")')
    lines.append("")
    lines.append("## Images")
    for bg in manifest.get("backgrounds", []):
        bg_id = bg["id"]
        bg_file = bg.get("image_file", bg_id[3:] + ".png" if bg_id.startswith("bg_") else bg_id + ".png")
        lines.append(f'image {bg_id} = "images/{bg_file}"')
    for it in manifest.get("items", []):
        lines.append(f'image {it["id"]} = "images/{it.get("image_file", it["id"] + ".png")}"')
    for char in manifest.get("characters", []):
        cid = char["id"]
        lines.append(f"image {cid}:")
        lines.append(f'    "images/{char.get("image_file", cid + ".png")}"')
        lines.append("    zoom 0.55")
    lines += ["", "transform left:", "    xalign 0.15 yalign 1.0",
              "transform center:", "    xalign 0.5 yalign 1.0",
              "transform right:", "    xalign 0.85 yalign 1.0",
              "transform speaking:", "    alpha 1.0",
              "transform not_speaking:", "    alpha 0.5", ""]
    return lines


def _find_pnc_issues(rooms_art: Dict, scripts: Dict[str, str],
                     valid_chars: Set[str]) -> Dict[str, str]:
    """Surface dangling jump/call targets in hotspot logic so the agent builds the missing
    piece rather than the build mutilating it. Mirrors _script._find_script_issues' intent."""
    room_ids = rooms_art.get("room_ids", [])
    rooms = rooms_art.get("rooms", {})
    node_ids = set(scripts.keys())
    valid_labels = set(room_ids) | node_ids | {"win", "start", "splashscreen"}
    for rid, room in rooms.items():
        for h in room.get("hotspots", []):
            valid_labels.add(_hs_label(rid, h.get("id", "")))
    issues = {}
    for rid, room in rooms.items():
        logic = "\n".join(h.get("logic", "") for h in room.get("hotspots", []))
        bad_jumps = set(_JUMP_RE.findall(logic)) - valid_labels
        bad_calls = set(_CALL_RE.findall(logic)) - valid_labels
        bad_chars = set(re.findall(r'\bshow\s+(\w+)', logic)) - valid_chars
        parts = []
        if bad_jumps:
            parts.append(f"jumps to missing labels {sorted(bad_jumps)} (make that room/hotspot/win)")
        if bad_calls:
            parts.append(f"calls missing dialogue nodes {sorted(bad_calls)} (create them in node_scripts)")
        if bad_chars:
            parts.append(f"shows undefined characters {sorted(bad_chars)}")
        if parts:
            issues[rid] = "; ".join(parts)
    return issues


def stitch_pnc(premise: Dict, manifest: Dict, rooms_art: Dict,
               scripts: Dict[str, str], valid_chars: Set[str]) -> Tuple[str, Dict[str, str]]:
    """Return (full_script, issues). If issues is non-empty the script is not trustworthy
    yet — the caller surfaces them as the agent's to-do instead of writing the project."""
    issues = _find_pnc_issues(rooms_art, scripts, valid_chars)
    if issues:
        return "", issues

    room_ids = rooms_art.get("room_ids", [])
    rooms = rooms_art.get("rooms", {})
    start = rooms_art.get("start_room") or (room_ids[0] if room_ids else "")
    flags = rooms_art.get("flags", []) or []

    out = _defines(premise, manifest)

    out.append("## State")
    out.append("default inventory = []")
    out.append(f'default current_room = "{start}"')
    for flag in flags:
        out.append(f"default {flag} = False")
    out.append("")

    out.append(_inventory_screen())
    for rid in room_ids:
        if rid in rooms:
            out.append(_room_screen(rid, rooms[rid]))

    out.append("label splashscreen:")
    out.append("    return")
    out.append("")
    out.append("label start:")
    out.append(f"    jump {start}")
    out.append("")
    out.append("label win:")
    out.append("    scene black")
    out.append('    centered "You won."')
    out.append("    return")
    out.append("")

    for rid in room_ids:
        if rid in rooms:
            out.append(_room_driver(rid, rooms[rid]))
            for h in rooms[rid].get("hotspots", []):
                out.append(_hotspot_logic_block(rid, h))

    for nid, script in scripts.items():
        if script:
            out.append(_postprocess_script(script, valid_chars))
            out.append("")

    return "\n".join(out), {}
