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
from typing import Dict, List

_IND = "    "


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


def node_block(node: Dict) -> List[str]:
    """`label <id>:` + the node's lines (with per-line effects) + its terminal control.
    Shared by the VN compiler and the point-and-click compiler (NPC dialogue nodes)."""
    out = [f"label {node['id']}:"]
    for line in node.get("lines", []):
        out.append(f"{_IND}{_line(line)}")
        for eff in line.get("effects", []):
            out.append(f"{_IND}{_effect(eff)}")
    out.extend(_end(node.get("end", {}), _IND))
    return out


def compile_vn(ir: Dict) -> str:
    """Return the Ren'Py script.rpy source for a visual_novel IR."""
    out: List[str] = []

    for c in ir.get("characters", []):
        out.append(f"define {c['id']} = Character({json.dumps(c['name'])})")
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

    for node in ir.get("nodes", []):
        out.extend(node_block(node))
        out.append("")

    return "\n".join(out).rstrip() + "\n"


if __name__ == "__main__":
    import sys
    ir = json.loads(open(sys.argv[1], encoding="utf-8").read())
    sys.stdout.write(compile_vn(ir))
