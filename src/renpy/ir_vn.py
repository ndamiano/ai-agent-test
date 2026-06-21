"""Compile a Game IR (genre: visual_novel) into a Ren'Py script.rpy.

The first IR backend. The point of the IR is that the model never writes Ren'Py — it writes
JSON, and THIS deterministic projection turns it into engine source. So every error class the
old hand-authored path fought (quote escaping, speaker format, menu indentation, dangling jumps)
is handled here, once, in code: dialogue strings go through json.dumps (correct escaping for
free), speakers are `<char_id> "text"` by construction, menus are emitted at fixed indentation.

Pure: IR dict in, script string out. Project packaging + SDK lint live elsewhere (renpy.fns /
renpy.compiler); this just produces the script body they'd write to game/script.rpy.
"""

import json
from typing import Callable, Dict, List, Optional

_IND = "    "

# Sprites are full-height art; bake a zoom into each character image so it sits at a natural
# stage height, and dim everyone but the current speaker (ported from the old text path).
_ZOOM = 0.55
_SPEAKING_ALPHA = 1.0
_DIMMED_ALPHA = 0.5


def _spread(n: int) -> List[float]:
    """Even xalign positions for n sprites on stage, so all are visible (never overlapping):
    one character -> centre; two -> thirds; etc. Deterministic in roster order."""
    return [round((i + 1) / (n + 1), 4) for i in range(n)]


def _num(n) -> str:
    return str(int(n)) if isinstance(n, (int, float)) and float(n).is_integer() else repr(n)


def _operand(val) -> str:
    return f'{val["var"]}' if isinstance(val, dict) else _num(val)


def _condition(cond: Dict) -> str:
    """Render a condition as a Ren'Py (python) boolean expression."""
    if "item" in cond:
        return f'{json.dumps(cond["item"])} in inventory'
    if "flag" in cond:
        return cond["flag"]
    if "var" in cond:
        return f'{cond["var"]} {cond["op"]} {_operand(cond["value"])}'
    if "not" in cond:
        return f"not ({_condition(cond['not'])})"
    if "all" in cond:
        return " and ".join(f"({_condition(c)})" for c in cond["all"])
    if "any" in cond:
        return " or ".join(f"({_condition(c)})" for c in cond["any"])
    return "True"


def _effect(eff: Dict) -> str:
    """Render a narrative effect as a Ren'Py `$` statement."""
    if "set_flag" in eff:
        return f'$ {eff["set_flag"]} = True'
    if "clear_flag" in eff:
        return f'$ {eff["clear_flag"]} = False'
    if "add_item" in eff:
        return f'$ inventory.append({json.dumps(eff["add_item"])})'
    if "remove_item" in eff:
        return f'$ if {json.dumps(eff["remove_item"])} in inventory: inventory.remove({json.dumps(eff["remove_item"])})'
    if "set_var" in eff:
        return f'$ {eff["set_var"]["var"]} = {_num(eff["set_var"]["value"])}'
    if "add_var" in eff:
        return f'$ {eff["add_var"]["var"]} += {_num(eff["add_var"]["delta"])}'
    return "pass"


def _line(line: Dict) -> str:
    text = json.dumps(line["text"])
    sp = line.get("speaker")
    return text if sp is None else f"{sp} {text}"


def _end(end: Dict, ind: str) -> List[str]:
    t = end.get("type")
    if t == "jump":
        return [f"{ind}jump {end['target']}"]
    if t == "menu":
        out = [f"{ind}menu:"]
        for ch in end.get("choices", []):
            head = json.dumps(ch["text"])
            if "requires" in ch:
                head += f" if {_condition(ch['requires'])}"
            out.append(f"{ind}{_IND}{head}:")
            for eff in ch.get("effects", []):
                out.append(f"{ind}{_IND}{_IND}{_effect(eff)}")
            out.append(f"{ind}{_IND}{_IND}jump {ch['target']}")
        return out
    # return (open-world loop) and end (definitive ending) both hand control back to Ren'Py.
    label = f"  # ending: {end['ending']}" if t == "end" and end.get("ending") else ""
    return [f"{ind}return{label}"]


def node_block(node: Dict, preamble: Optional[List[str]] = None,
               restage: Optional[Callable[[str, str], List[str]]] = None) -> List[str]:
    """`label <id>:` + optional staging preamble (scene/show) + the node's lines (with per-line
    effects) + its terminal control. `restage(speaker, emotion)` (VN only) returns the show-lines
    that re-stage the cast — swapping the speaker to their line's expression, brightening them, and
    dimming the rest — emitted before each spoken line. Shared with the point-and-click compiler
    (NPC dialogue nodes pass neither: they play over the place they were called from)."""
    out = [f"label {node['id']}:"]
    out.extend(preamble or [])
    for line in node.get("lines", []):
        sp = line.get("speaker")
        if restage and sp:
            out.extend(restage(sp, line.get("emotion") or "neutral"))
        out.append(f"{_IND}{_line(line)}")
        for eff in line.get("effects", []):
            out.append(f"{_IND}{_effect(eff)}")
    out.extend(_end(node.get("end", {}), _IND))
    return out


def _staging(ir: Dict):
    """Build the staging closure: per node, the preamble (scene + initial shows) and a
    `restage(speaker, emotion)` for speaker highlighting + expression swaps. Returns
    (stage_fn, image_decl_lines)."""
    bg_files = {bg["id"]: bg["image_file"] for bg in ir.get("backgrounds", [])}

    # Per character: {emotion: image_file}. `expressions` is authoritative; fall back to the
    # neutral `sprite` so a single-sprite character still stages.
    variants: Dict[str, Dict[str, str]] = {}
    for c in ir.get("characters", []):
        exprs = dict(c.get("expressions") or {})
        if not exprs and c.get("sprite"):
            exprs = {"neutral": c["sprite"]}
        if exprs:
            variants[c["id"]] = exprs

    def _img(cid: str, emotion: str) -> str:
        # Stable per-character displayable + tag: `char_<cid>_<emotion>` shown `as char_<cid>`,
        # so swapping expression replaces in place instead of stacking a second sprite.
        exprs = variants[cid]
        return f"char_{cid}_{emotion if emotion in exprs else 'neutral'}"

    decls: List[str] = []
    for bid, fname in bg_files.items():
        decls.append(f'image {bid} = "images/{fname}"')
    for cid, exprs in variants.items():
        for emotion, fname in exprs.items():
            decls.append(f"image char_{cid}_{emotion}:")
            decls.append(f'{_IND}"images/{fname}"')
            decls.append(f"{_IND}zoom {_ZOOM}")

    def stage(node: Dict, current_bg: Optional[str]):
        # roster: distinct characters who speak in this node (and have a sprite), in order.
        # first_emotion: each roster member's earliest spoken expression (neutral default).
        roster: List[str] = []
        first_emotion: Dict[str, str] = {}
        for line in node.get("lines", []):
            sp = line.get("speaker")
            if sp in variants:
                if sp not in roster:
                    roster.append(sp)
                    first_emotion[sp] = line.get("emotion") or "neutral"
        posmap = dict(zip(roster, _spread(len(roster))))
        current = dict(first_emotion)

        # Reset the stage at every node so sprites from the previous scene never linger into this
        # one. `scene` clears all shown images; it also (re)sets the background — the node's own
        # location when it tags one, else the last known background carried forward (an untagged
        # node continues the prior scene rather than blanking it).
        loc = node.get("location")
        bg = loc if loc in bg_files else current_bg
        pre: List[str] = [f"{_IND}scene {bg}" if bg else f"{_IND}scene"]
        # Show every speaker up front at their first expression, spread so all are visible.
        for cid, x in posmap.items():
            pre.append(f"{_IND}show {_img(cid, current[cid])} as char_{cid} at stage({x})")

        def restage(speaker: str, emotion: str) -> List[str]:
            changed = current.get(speaker) != emotion
            if speaker in current:
                current[speaker] = emotion
            # One character on stage: nobody to dim, so re-show only to swap expression.
            if len(posmap) < 2 and not changed:
                return []
            return [f"{_IND}show {_img(cid, current[cid])} as char_{cid} at stage({x}), "
                    f"{'speaking' if cid == speaker else 'not_speaking'}"
                    for cid, x in posmap.items()]

        return pre, restage, bg

    return stage, decls


def compile_vn(ir: Dict) -> str:
    """Return the Ren'Py script.rpy source for a visual_novel IR."""
    out: List[str] = []

    stage, image_decls = _staging(ir)

    for c in ir.get("characters", []):
        out.append(f"define {c['id']} = Character({json.dumps(c['name'])})")
    out.append("")

    out.append("transform stage(x):")
    out.append(f"{_IND}xalign x")
    out.append(f"{_IND}yalign 1.0")
    out.append("transform speaking:")
    out.append(f"{_IND}alpha {_SPEAKING_ALPHA}")
    out.append("transform not_speaking:")
    out.append(f"{_IND}alpha {_DIMMED_ALPHA}")
    out.append("")

    if image_decls:
        out.extend(image_decls)
        out.append("")

    for f in ir.get("flags", []):
        out.append(f"default {f} = False")
    for v in ir.get("variables", []):
        out.append(f"default {v['id']} = {_num(v['default'])}")
    if ir.get("items"):
        out.append("default inventory = []")
    out.append("")

    out.append("label start:")
    out.append(f"{_IND}jump {ir['start']['node']}")
    out.append("")

    current_bg: Optional[str] = None
    for node in ir.get("nodes", []):
        pre, restage, current_bg = stage(node, current_bg)
        out.extend(node_block(node, pre, restage))
        out.append("")

    return "\n".join(out).rstrip() + "\n"


if __name__ == "__main__":
    import sys
    ir = json.loads(open(sys.argv[1], encoding="utf-8").read())
    sys.stdout.write(compile_vn(ir))
