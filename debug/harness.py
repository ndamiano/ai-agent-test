"""Prompt-iteration harness — one driver for every build mode.

Reproduces ONE production LLM call for a chosen mode (same system prompt, same rendered context,
same tool schema) so you can hand-climb that mode's prompt against the real local model without a
full build. Adding a mode = one line in MODES (+ a show fn if its output shape is new), not a new
file.

  python debug/harness.py <mode> [N]        N fresh calls from the LIVE prompt (default 1)
  python debug/harness.py <mode> dump       write exact assembled system/user to work/<mode>_*.txt
  python debug/harness.py <mode> send N      replay work/<mode>_system.txt + _user.txt VERBATIM
  python debug/harness.py reseed             (re)propose+cache the spec from work/request.txt

A mode's only inputs are the spec + its upstream components. The spec is taken from
work/seed_spec.json if present (see `reseed`), else fixtures/spec.json; upstream components are
copied from fixtures/. So you iterate ONLY that mode's prompt.

  modes: premise, outline, asset, nodes   (extend MODES for places/matches once fixtures exist)
"""
import json, shutil, sys, uuid
from dataclasses import dataclass
from typing import Callable, Optional
import _bootstrap as B
from maestro.spec import Spec
from maestro.state import RunState
from maestro.tools import build_tools, tool_schemas_for
from maestro.modules import compose, modules_for
from maestro.executor import Executor
from maestro.agent import _render_context, _load_prompt
from renpy.component_schemas import SCHEMAS as ir_schemas, skeleton_guide
from renpy.ir_checks import node_view, place_view
from llm_clients.connector_selector import get_connector

REQ_F = B.WORK / "request.txt"
SEED_SPEC = B.WORK / "seed_spec.json"
PROJECTORS = {"nodes": node_view, "places": place_view}
DEFAULT_REQ = ("Make a visual novel about two former bandmates, now strangers after a bitter "
               "breakup, forced to share a cramped overnight train compartment as they travel to "
               "scatter their late drummer's ashes, and must confront whose fault the band's "
               "collapse really was")


# ---- result display, per output shape -------------------------------------------------------
def _content(args):
    c = args.get("content", args)
    return c.get(args.get("component_id", ""), c) if isinstance(c, dict) else c


def show_premise(args):
    p = _content(args)
    print("CENTRAL QUESTION:", p.get("central_question"))
    print("-" * 70)
    for c in p.get("characters", []):
        print(f"[{c.get('id')}] {c.get('name')} — {c.get('temperament')}")
        print("  voice:", c.get("voice"))
        print("  drive:", c.get("drive"))
        for ln in c.get("example_lines", []):
            print("   ·", ln)
    print("ENDINGS:")
    for e in p.get("endings", []):
        print(f"  [{e.get('id')}] {e.get('description') or e.get('summary')}")


def show_outline(args):
    o = _content(args)
    print("LOGLINE:", o.get("logline"))
    print("-" * 70)
    for b in o.get("beats", []):
        print(f"[{b.get('id')}] {b.get('purpose')} — {b.get('summary')}")
        print("   tension:", b.get("tension"))
    print("ENDING PATHS:")
    for ep in o.get("ending_paths", []):
        print(f"  {ep.get('ending')} <- {ep.get('earned_by')}")


def show_node(args):
    c = args.get("content", args)
    print("node_id:", args.get("node_id"), "| beat:", c.get("beat"), "| loc:", c.get("location"))
    print("-" * 70)
    for ln in c.get("lines", []):
        sp = ln.get("speaker") or "NARRATION"
        emo = ln.get("emotion")
        print(f"  {sp}{'/' + emo if emo else ''}: {ln.get('text')}")
    e = c.get("end", {}) or {}
    if e.get("type") == "menu":
        for ch in e.get("choices", []):
            print(f"   > [{ch.get('text')}] -> {ch.get('target')}")
    else:
        print("   -> jump", e.get("target"))


def show_json(args):
    print(json.dumps(_content(args), ensure_ascii=False, indent=2)[:2000])


@dataclass
class Mode:
    component: str               # component id build_context must resolve to
    tool: str                    # tool whose schema is offered (write_component / write_node / ...)
    show: Callable               # how to print the result


MODES = {
    "premise": Mode("premise", "write_component", show_premise),
    "outline": Mode("outline", "write_component", show_outline),
    "asset":   Mode("asset_manifest", "write_component", show_json),
    "nodes":   Mode("nodes", "write_node", show_node),
}


# ---- harness core ---------------------------------------------------------------------------
def spec_source():
    return SEED_SPEC if SEED_SPEC.exists() else B.FIXTURES / "spec.json"


def reseed():
    if not REQ_F.exists():
        REQ_F.write_text(DEFAULT_REQ)
    req = REQ_F.read_text().strip()
    from maestro.spec_tools import propose_spec
    spec = propose_spec(req, "seed_" + uuid.uuid4().hex[:6])
    SEED_SPEC.write_text(json.dumps(spec, ensure_ascii=False, indent=2))
    print("reseeded from request:\n ", req[:90], "...\n  modules:", spec.get("modules"))


def seed_run(target: str):
    """Fresh run with spec + every upstream component (deps before `target`), so build_context
    resolves to `target` as the earliest unwritten component."""
    spec = Spec(json.loads(spec_source().read_text()))
    rid = f"{target}_" + uuid.uuid4().hex[:6]
    rd = RunState.for_run(rid).run_dir
    shutil.copy(spec_source(), rd / "spec.json")
    for cid in spec.dep_order():
        if cid == target:
            break
        f = B.FIXTURES / f"{cid}.json"
        if f.exists():
            shutil.copy(f, rd / f.name)
    return rid


def assemble(mode: Mode):
    state = RunState.for_run(seed_run(mode.component))
    spec = Spec(state.read_spec())
    composed = compose(modules_for(spec.data))
    tools = build_tools(spec, state, schemas=ir_schemas)
    projector = PROJECTORS.get(mode.component)
    ex = Executor(spec, state, tools, decide=lambda c: None,
                  projectors={mode.component: projector} if projector else {},
                  upstream_views=composed.context_views)
    ctx = ex.build_context()
    assert ctx["mode"] == mode.component, f"mode={ctx['mode']} (expected {mode.component})"
    if mode.component in composed.subloop_modules:
        ctx["target"] = {"check": {"type": "count", "path": mode.component}}
    system = _load_prompt(composed.mode_prompts[mode.component]) + "\n\n" + \
        skeleton_guide(component_ids=[mode.component])
    return spec, system, _render_context(ctx)


def call(spec, mode: Mode, system, user, reasoning="none"):
    schemas = [s for s in tool_schemas_for(spec)
               if s.get("function", {}).get("name") == mode.tool]
    conn = get_connector()
    msgs = [{"role": "system", "content": system}, {"role": "user", "content": user}]
    resp = conn.generate_with_tools(msgs, schemas, reasoning=reasoning, max_tokens=8000)
    msg = (resp.get("choices") or [{}])[0].get("message", {})
    tcs = msg.get("tool_calls") or []
    if tcs:
        return json.loads(tcs[0]["function"]["arguments"])
    txt = msg.get("content") or ""              # rescue prose-JSON (no-tool-call failure mode)
    s, e = txt.find("{"), txt.rfind("}")
    if s >= 0 and e > s:
        try:
            return json.loads(txt[s:e + 1])
        except Exception:
            print("!! prose, unparseable:\n", txt[:1500]); return None
    print("!! no content:\n", txt[:800]); return None


def main():
    if len(sys.argv) >= 2 and sys.argv[1] == "reseed":
        return reseed()
    if len(sys.argv) < 2 or sys.argv[1] not in MODES:
        sys.exit(f"usage: python debug/harness.py <{'|'.join(MODES)}|reseed> [N|dump|send N]")
    mode = MODES[sys.argv[1]]
    sys_f = B.WORK / f"{sys.argv[1]}_system.txt"
    usr_f = B.WORK / f"{sys.argv[1]}_user.txt"
    cmd = sys.argv[2] if len(sys.argv) > 2 else "1"

    if cmd == "dump":
        _, system, user = assemble(mode)
        sys_f.write_text(system); usr_f.write_text(user)
        print("wrote:\n ", sys_f, f"({len(system)} chars)\n ", usr_f, f"({len(user)} chars)")
    elif cmd == "send":
        n = int(sys.argv[3]) if len(sys.argv) > 3 else 1
        spec = Spec(json.loads(spec_source().read_text()))
        system, user = sys_f.read_text(), usr_f.read_text()
        for i in range(n):
            print(f"\n========== SEND {i + 1} ==========")
            mode.show(call(spec, mode, system, user) or {})
    else:
        for i in range(int(cmd)):
            print(f"\n========== ATTEMPT {i + 1} ==========")
            spec, system, user = assemble(mode)
            mode.show(call(spec, mode, system, user) or {})


if __name__ == "__main__":
    main()
