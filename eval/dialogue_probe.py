"""Dialogue prompt probe — NOT an automated judge/climb harness.

A thin generate-and-log tool: fire ONE write_node dialogue call at LM Studio with a
given prompt variant against a FIXED scene context, and save the full input + output to
disk for a human to read and compare. The intelligence in the loop is the human reading
the outputs, not an LLM judge (which, per hard experience, can't reliably grade prose at
this model size). Only the prompt varies; everything else is held constant so differences
are attributable to the prompt.

Usage:
  # capture a fixed scene context from a real run (do once)
  python eval/dialogue_probe.py capture <run_dir> [--beat-index 1] [--name echoes_beat02]

  # run a prompt variant against the fixture N times, log everything
  python eval/dialogue_probe.py run --prompt src/maestro/prompts/write_node.txt \
      --fixture eval/fixtures/dialogue/echoes_beat02.json --samples 3 [--label baseline]
"""
import sys
import json
import argparse
import datetime
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
_SRC = _ROOT / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

_FIXTURE_DIR = _ROOT / "eval" / "fixtures" / "dialogue"
_OUT_DIR = _ROOT / "eval" / "probe_runs"

import re  # noqa: E402
from maestro.agent import _render_context  # noqa: E402
from maestro.tools import TOOL_SCHEMAS, _coerce_json, _node_content_error  # noqa: E402
from llm_clients.inference import strip_fences  # noqa: E402
from renpy.ir_checks import node_view  # noqa: E402
from renpy.component_schemas import skeleton_guide  # noqa: E402
from renpy.templating import render_template  # noqa: E402
from llm_clients.connector_selector import get_connector  # noqa: E402
from llm_clients.message_builder import MessageBuilder  # noqa: E402

_WRITE_NODE_SCHEMA = next(s for s in TOOL_SCHEMAS
                          if s["function"]["name"] == "write_node")


def _read_json(p: Path):
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None


def capture(run_dir: str, beat_index: int, name: str) -> None:
    """Snapshot a real run into a fixed single-scene authoring task: premise + outline +
    asset_manifest + story_state, plus the OPENING scene as already-written, with the
    target scene (the next beat) left as an OPEN SLOT to generate."""
    rd = Path(run_dir)
    premise = _read_json(rd / "premise.json")
    outline = _read_json(rd / "outline.json")
    manifest = _read_json(rd / "asset_manifest.json")
    story_state = _read_json(rd / "story_state.json") or {}
    nodes = _read_json(rd / "nodes.json")
    if not (premise and outline and nodes):
        sys.exit("run dir missing premise/outline/nodes")

    beats = [b["id"] for b in outline.get("beats", []) if b.get("id")]
    if beat_index >= len(beats):
        sys.exit(f"beat-index {beat_index} out of range ({len(beats)} beats)")
    target_beat = beats[beat_index]
    target_slot = "scene_target"

    entry_id = nodes["node_ids"][0]
    entry = dict(nodes["nodes"][entry_id])
    # Repoint the opening scene at the slot we want generated, so node_view yields it as the
    # one open slot with a path breadcrumb and the target beat shows as not-yet-realized.
    entry["end"] = {"type": "jump", "target": target_slot}
    entry.setdefault("beat", beats[0])
    syn = nodes.get("synopses", {}).get(entry_id, "")

    fixture = {
        "name": name,
        "source_run": str(rd),
        "target_slot": target_slot,
        "target_beat": target_beat,
        "components": {
            "premise": premise,
            "outline": outline,
            "asset_manifest": manifest,
            "nodes": {
                "node_ids": [entry_id],
                "nodes": {entry_id: entry},
                "synopses": {entry_id: syn},
            },
        },
        "story_state": story_state,
    }
    _FIXTURE_DIR.mkdir(parents=True, exist_ok=True)
    out = _FIXTURE_DIR / f"{name}.json"
    out.write_text(json.dumps(fixture, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"captured -> {out}")
    print(f"  target: write {target_slot} (beat {target_beat}) after {entry_id}")


def _artifact(fixture: dict) -> dict:
    return dict(fixture["components"])


def _ctx(fixture: dict) -> dict:
    """Reconstruct the context a real nodes/author step sends — upstream locked components,
    story_state, the live node graph view (open slot + beats_todo), and the target check."""
    art = _artifact(fixture)
    upstream = {k: v for k, v in art.items() if k != "nodes"}
    view = node_view(art)
    target = {"component_id": "nodes", "check": {"type": "beats_realized"},
              "detail": f"outline beats with no scene yet: ['{fixture['target_beat']}'] — "
                        f"write a node that dramatizes each (set the node's `beat`)."}
    return {
        "spec": {"title": fixture["components"]["premise"].get("title", "probe"),
                 "request": "dialogue probe", "components": [
                     {"id": "nodes", "description": "the dialogue graph",
                      "done_conditions": [{"type": "beats_realized"}]}]},
        "mode": "nodes",
        "todo": [target],
        "target": target,
        "upstream": upstream,
        "active_view": view,
        "scratchpad": {},
        "story_state": fixture.get("story_state", {}),
    }


def _generate(prompt_path: Path, fixture: dict, reasoning: str, max_tokens: int):
    system = render_template(prompt_path, {}) + "\n\n" + skeleton_guide(component_ids=["nodes"])
    user = _render_context(_ctx(fixture))
    messages = MessageBuilder(system).add_user(user).build()
    conn = get_connector()
    resp = conn.generate_with_tools(messages, [_WRITE_NODE_SCHEMA], reasoning=reasoning,
                                    max_tokens=max_tokens)
    node, err, channel = None, None, None
    if "error" in resp:
        err = resp["error"]
        return system, user, resp, node, err, channel
    msg = (resp.get("choices") or [{}])[0].get("message", {})
    calls = [c for c in (msg.get("tool_calls") or []) if c.get("function", {}).get("name") == "write_node"]
    if calls:
        channel = "tool_call"
        args = calls[0]["function"].get("arguments")
        args = _coerce_json(args) if isinstance(args, str) else (args or {})
        node = _coerce_json(args.get("content")) if isinstance(args, dict) else None
    else:
        # The small model often dumps the JSON in the text channel instead of calling the tool
        # (prod nudges past this; the probe salvages it so the prose is still readable).
        channel = "content"
        node = _salvage_node(msg.get("content") or "")
    if not isinstance(node, dict):
        err = "no parseable node in tool_call or content"
    else:
        guard = _node_content_error(node)
        if guard:
            err = f"build-invalid ({guard})"   # still readable below; just wouldn't pass write_node
    return system, user, resp, node, err, channel


def _salvage_node(text: str):
    """Pull a node object out of free-text the model returned instead of a tool call. Handles the
    {node_id, content:{...}} wrapper and a bare {lines, end} object; tolerates truncation by
    grabbing the lines array even when the trailing JSON is cut off."""
    s = strip_fences(text).strip()
    for parsed in (_try_json(s), _try_json(_first_brace(s))):
        if isinstance(parsed, dict):
            return parsed.get("content") if isinstance(parsed.get("content"), dict) else parsed
    # Truncated JSON — recover just the lines array so the dialogue is still readable.
    m = re.search(r'"lines"\s*:\s*\[', s)
    if m:
        frag = s[m.start():]
        objs = re.findall(r'\{[^{}]*"text"\s*:\s*"(?:[^"\\]|\\.)*"[^{}]*\}', frag)
        lines = [p for o in objs if (p := _try_json(o))]
        if lines:
            return {"lines": lines, "end": {"type": "(truncated)"}}
    return None


def _try_json(s):
    try:
        return json.loads(s)
    except Exception:
        return None


def _first_brace(s: str) -> str:
    i, j = s.find("{"), s.rfind("}")
    return s[i:j + 1] if 0 <= i < j else s


def _fmt_lines(node) -> str:
    if not isinstance(node, dict):
        return "(no parsed node)"
    out = []
    for ln in node.get("lines", []):
        sp = ln.get("speaker") or "narration"
        emo = f" ({ln['emotion']})" if ln.get("emotion") else ""
        out.append(f"  {sp}{emo}: {ln.get('text','')}")
    end = node.get("end", {})
    out.append(f"  [end: {end.get('type')}{' -> ' + end.get('target','') if end.get('target') else ''}]")
    return "\n".join(out)


def run(prompt: str, fixture_path: str, samples: int, label: str, reasoning: str,
        max_tokens: int) -> None:
    fixture = _read_json(Path(fixture_path))
    if not fixture:
        sys.exit(f"no fixture at {fixture_path}")
    prompt_path = Path(prompt)
    label = label or prompt_path.stem
    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    run_dir = _OUT_DIR / f"{ts}_{label}"
    run_dir.mkdir(parents=True, exist_ok=True)

    md = [f"# probe: {label}", f"- prompt: `{prompt}`", f"- fixture: `{fixture_path}` "
          f"(write {fixture['target_slot']}, beat {fixture['target_beat']})",
          f"- samples: {samples}  reasoning: {reasoning}", ""]
    for i in range(samples):
        print(f"[{label}] sample {i+1}/{samples} ...", flush=True)
        system, user, resp, node, err, channel = _generate(prompt_path, fixture, reasoning, max_tokens)
        rec = {"label": label, "prompt": prompt, "sample": i, "channel": channel,
               "system": system, "user": user, "raw_response": resp, "node": node, "error": err}
        (run_dir / f"sample_{i:02d}.json").write_text(
            json.dumps(rec, indent=2, ensure_ascii=False), encoding="utf-8")
        tag = f"channel={channel}" + (f"  NOTE: {err}" if err else "")
        body = _fmt_lines(node) if isinstance(node, dict) else (err or "(nothing)")
        md += [f"## sample {i}  ({tag})", "```", body, "```", ""]
        print(f"-- sample {i} [{tag}]\n{body}\n")
    # The shared system/user (constant across samples) saved once for research.
    (run_dir / "context.txt").write_text(
        f"===== SYSTEM =====\n{system}\n\n===== USER =====\n{user}\n", encoding="utf-8")
    (run_dir / "summary.md").write_text("\n".join(md), encoding="utf-8")
    print(f"saved -> {run_dir}")


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("capture")
    c.add_argument("run_dir")
    c.add_argument("--beat-index", type=int, default=1)
    c.add_argument("--name", default="probe_scene")
    r = sub.add_parser("run")
    r.add_argument("--prompt", required=True)
    r.add_argument("--fixture", required=True)
    r.add_argument("--samples", type=int, default=3)
    r.add_argument("--label", default="")
    r.add_argument("--reasoning", default="none")
    r.add_argument("--max-tokens", type=int, default=8000)
    a = ap.parse_args()
    if a.cmd == "capture":
        capture(a.run_dir, a.beat_index, a.name)
    else:
        run(a.prompt, a.fixture, a.samples, a.label, a.reasoning, a.max_tokens)


if __name__ == "__main__":
    main()
