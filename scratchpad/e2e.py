#!/usr/bin/env python3
"""Full pipeline: user request -> model drafts SPEC -> model writes CODE -> gates.
   python3 e2e.py <slug> "<user request>" [rounds]
No hand-written spec. This is the real chat->game loop (auto-freeze for the test)."""
import json, re, subprocess, sys
from pathlib import Path
from codegen_test import chat, extract_code, headless, RT

MODEL = "Qwen3-Coder-30B-A3B-Instruct-Q4_K_M"

SPEC_SYSTEM = (
    "You are a game designer. Given a one-line request, you write a DESIGN SPEC as JSON that a "
    "programmer will build from. Invent the concrete specifics the request leaves open. Output "
    "ONLY one ```json block, this exact shape:\n"
    '{\n'
    '  "title": "...", "genre": "...",\n'
    '  "screen": {"w": 800, "h": 600},\n'
    '  "entities": [ {"id": "...", "desc": "one concrete sentence: what it is, how it moves, its size/look"} ],\n'
    '  "controls": {"KEY": "what it does"},\n'
    '  "mechanics": ["one rule per line: spawning, collision outcomes, scoring, difficulty"],\n'
    '  "win": "the win condition (or null for an endless arcade game)",\n'
    '  "lose": "the lose condition",\n'
    '  "render": "one line: how each thing is drawn (shapes + colors)"\n'
    '}\n'
    "Keep it buildable from kit primitives (rects, circles, lines, velocity, spawn, rng, screen "
    "wrap done by hand). No assets, no networking, no text input. Be concrete, not exhaustive."
)

def draft_spec(request):
    reply = chat(MODEL, [{"role": "system", "content": SPEC_SYSTEM},
                         {"role": "user", "content": f"Request: {request}\n\nWrite the JSON spec."}],
                 max_tokens=4000)
    m = re.search(r"```(?:json)?\s*\n(.*?)```", reply, re.S)
    raw = m.group(1) if m else reply
    return json.loads(raw)

def probe(slug):
    p = subprocess.run(["node", "probe.mjs", f"games/{slug}.js"], cwd=RT, capture_output=True, text=True, timeout=60)
    try: return json.loads(p.stdout)
    except Exception: return {"ok": False, "violations": [{"kind": "runner", "detail": p.stderr[-400:]}]}

def author(slug, spec, api):
    system = ("You are a game programmer. Write ONE complete JavaScript ES module building the game "
              "against the kit API. Output ONLY a ```js block. `export function createGame(kit)` "
              "returning the kit's game shape. Use ONLY documented kit primitives; keep state in "
              "this.state with entities in this.state.world; never draw in update; never mutate in draw. "
              "Screen-wrap and rotation you implement by hand with the math primitives.")
    task = f"# KIT API\n{api}\n\n# DESIGN SPEC\n```json\n{json.dumps(spec, indent=1)}\n```\n\nAuthor the module now."
    return [{"role": "system", "content": system}, {"role": "user", "content": task}]

def main():
    slug = sys.argv[1]; request = sys.argv[2]; rounds = int(sys.argv[3]) if len(sys.argv) > 3 else 5
    api = (RT / "kit_api.md").read_text()
    outfile = RT / "games" / f"{slug}.js"

    print(f"== STAGE 1: draft spec from request: {request!r} ==", flush=True)
    spec = draft_spec(request)
    (RT / "specs" / f"{slug}.json").write_text(json.dumps(spec, indent=2))
    print(json.dumps(spec, indent=1), flush=True)
    print(f"\n== STAGE 2: author + gate ==", flush=True)

    messages = author(slug, spec, api)
    for rnd in range(1, rounds + 1):
        reply = chat(MODEL, messages, max_tokens=16000)
        outfile.write_text(extract_code(reply))
        hl = headless(slug); pr = probe(slug)
        print(f"  round {rnd}: headless={hl.get('ok')} probe={pr.get('ok')}", flush=True)
        if hl.get("ok") and pr.get("ok"):
            print(f"\nSUCCESS in {rnd} round(s): spec+code both from the model, gates green."); return
        fails = ([f"HEADLESS: {json.dumps(hl)}"] if not hl.get("ok") else []) + \
                [f"CORRECTNESS [{v['kind']}]: {v['detail']}" for v in pr.get("violations", [])]
        print("   feeding back:", " || ".join(f[:110] for f in fails), flush=True)
        messages += [{"role": "assistant", "content": f"```js\n{outfile.read_text()}\n```"},
                     {"role": "user", "content": "Failures:\n" + "\n".join(f"- {f}" for f in fails) +
                      "\nFix all; keep what works; output the complete corrected ```js module."}]
    print(f"\nFAILED after {rounds} rounds.")

if __name__ == "__main__":
    main()
