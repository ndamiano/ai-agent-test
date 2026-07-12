#!/usr/bin/env python3
"""Full 3D pipeline: request -> model drafts a 3D spec -> model writes 3D sim code -> gates.
   python3 e2e3d.py <slug> "<request>" [rounds]
The gates (headless + probe) run the PURE SIM — identical to 2D — because a 3D game's update()
is still plain x/y/z math. Rendering (three.js) is browser-only and not gated here."""
import json, re, subprocess, sys
from pathlib import Path
from codegen_test import chat, extract_code, headless, RT

MODEL = "Qwen3-Coder-30B-A3B-Instruct-Q4_K_M"

SPEC_SYSTEM = (
    "You are a 3D game designer. Given a one-line request, write a DESIGN SPEC as JSON for a simple "
    "real-time 3D game. Output ONLY one ```json block, this shape:\n"
    '{\n'
    '  "title": "...", "genre": "...",\n'
    '  "entities": [ {"id":"...","shape":"box|sphere|ground","desc":"what it is, how it moves in 3D, its size/color"} ],\n'
    '  "controls": {"KEY":"what it does in 3D space"},\n'
    '  "camera": "how the camera behaves (e.g. third-person follow behind the player)",\n'
    '  "mechanics": ["one rule per line: 3D movement, collision by distance, scoring, win/lose"],\n'
    '  "win": "win condition or null", "lose": "lose condition"\n'
    '}\n'
    "Coordinates: +y is UP, ground is the y=0 plane, scene is tens of units across. Keep it buildable "
    "from boxes/spheres/a ground plane. Be concrete, not exhaustive."
)

def draft_spec(request):
    reply = chat(MODEL, [{"role": "system", "content": SPEC_SYSTEM},
                         {"role": "user", "content": f"Request: {request}\n\nWrite the JSON spec."}], max_tokens=4000)
    m = re.search(r"```(?:json)?\s*\n(.*?)```", reply, re.S)
    return json.loads(m.group(1) if m else reply)

def probe(slug):
    p = subprocess.run(["node", "probe.mjs", f"games/{slug}.js"], cwd=RT, capture_output=True, text=True, timeout=60)
    try: return json.loads(p.stdout)
    except Exception: return {"ok": False, "violations": [{"kind": "runner", "detail": p.stderr[-400:]}]}

def author_msgs(spec, api):
    system = ("You are a 3D game programmer. Write ONE complete JavaScript ES module building the game "
              "against the 3D kit API. Output ONLY a ```js block. `export function createGame(kit)` "
              "returning the kit's 3D game shape with config.mode='3d'. You write PURE SIMULATION and "
              "tag entities with a shape; you write NO three.js. Keep state in this.state with entities "
              "in this.state.world; never render in update. Use kit.integrate3 for motion (units/second, "
              "dt handled); do NOT use per-frame magnitudes.")
    task = f"# 3D KIT API\n{api}\n\n# DESIGN SPEC\n```json\n{json.dumps(spec, indent=1)}\n```\n\nAuthor the module now."
    return [{"role": "system", "content": system}, {"role": "user", "content": task}]

def main():
    slug, request = sys.argv[1], sys.argv[2]
    rounds = int(sys.argv[3]) if len(sys.argv) > 3 else 6
    api = (RT / "kit_api_3d.md").read_text()
    outfile = RT / "games" / f"{slug}.js"

    print(f"== STAGE 1: draft 3D spec: {request!r} ==", flush=True)
    spec = draft_spec(request)
    (RT / "specs" / f"{slug}.json").write_text(json.dumps(spec, indent=2))
    print(json.dumps(spec, indent=1), flush=True)
    print("\n== STAGE 2: author + gate (pure sim) ==", flush=True)

    messages = author_msgs(spec, api)
    for rnd in range(1, rounds + 1):
        reply = chat(MODEL, messages, max_tokens=16000)
        outfile.write_text(extract_code(reply))
        hl = headless(slug); pr = probe(slug)
        print(f"  round {rnd}: headless={hl.get('ok')} probe={pr.get('ok')}", flush=True)
        if hl.get("ok") and pr.get("ok"):
            print(f"\nSUCCESS in {rnd} round(s)."); return
        fails = ([f"HEADLESS: {json.dumps(hl)}"] if not hl.get("ok") else []) + \
                [f"CORRECTNESS [{v['kind']}]: {v['detail']}" for v in pr.get("violations", [])]
        print("   feeding back:", " || ".join(f[:110] for f in fails), flush=True)
        messages += [{"role": "assistant", "content": f"```js\n{outfile.read_text()}\n```"},
                     {"role": "user", "content": "Failures:\n" + "\n".join(f"- {f}" for f in fails) +
                      "\nFix all; keep what works; output the complete corrected ```js module."}]
    print(f"\nFAILED after {rounds} rounds.")

if __name__ == "__main__":
    main()
