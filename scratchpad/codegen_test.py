#!/usr/bin/env python3
"""Codegen loop test: local model authors a game against the kit, headless gates it.

  python3 codegen_test.py pacman [model] [rounds]

Writes runtime/games/<slug>.js, runs runtime/headless.mjs, and on a crash feeds the
error back to the model. This is the real experiment: can a local coder model compose
the kit into a working game.
"""
import json, re, subprocess, sys, time, urllib.request
from pathlib import Path

RT = Path(__file__).resolve().parent.parent / "runtime"
BASE = "http://localhost:8080"

def chat(model, messages, max_tokens=16000, timeout=600):
    body = json.dumps({"model": model, "messages": messages,
                       "temperature": 0.3, "max_tokens": max_tokens}).encode()
    req = urllib.request.Request(f"{BASE}/v1/chat/completions", data=body,
                                 headers={"Content-Type": "application/json"})
    t = time.time()
    with urllib.request.urlopen(req, timeout=timeout) as r:
        d = json.load(r)
    dt = time.time() - t
    msg = d["choices"][0]["message"]["content"]
    usage = d.get("usage", {})
    print(f"  [llm {dt:.0f}s, {usage.get('completion_tokens','?')} out toks]", flush=True)
    return msg

def extract_code(text):
    m = re.search(r"```(?:js|javascript)?\s*\n(.*?)```", text, re.S)
    code = m.group(1) if m else text
    return code.strip()

def headless(slug, frames=900):
    p = subprocess.run(["node", "headless.mjs", f"games/{slug}.js", str(frames)],
                       cwd=RT, capture_output=True, text=True, timeout=60)
    try:
        return json.loads(p.stdout.strip().splitlines()[-1])
    except Exception:
        return {"ok": False, "phase": "runner", "error": (p.stdout + p.stderr)[-800:]}

def main():
    slug = sys.argv[1] if len(sys.argv) > 1 else "pacman"
    model = sys.argv[2] if len(sys.argv) > 2 else "Qwen3-Coder-30B-A3B-Instruct-Q4_K_M"
    rounds = int(sys.argv[3]) if len(sys.argv) > 3 else 5

    api = (RT / "kit_api.md").read_text()
    spec = (RT / "specs" / f"{slug}.json").read_text()
    system = ("You are a game programmer. You write ONE complete JavaScript ES module that builds "
              "the requested game against the provided kit API. Output ONLY the module as a single "
              "```js code block — no prose. The module MUST `export function createGame(kit)` "
              "returning the game object exactly as the kit shape shows. Use ONLY kit primitives "
              "documented below; do not invent kit functions. Keep all state in `this.state`, put "
              "entities in `this.state.world`, never draw in update, never mutate in draw.")
    task = (f"# KIT API\n{api}\n\n# GAME DESIGN SPEC\n```json\n{spec}\n```\n\n"
            f"Author the complete module now. Build the maze with kit.makeTilemap and use "
            f"solidsNear/solidAt for wall collision. Output only the ```js block.")
    messages = [{"role": "system", "content": system}, {"role": "user", "content": task}]

    outfile = RT / "games" / f"{slug}.js"
    for rnd in range(1, rounds + 1):
        print(f"== round {rnd} ==", flush=True)
        reply = chat(model, messages)
        code = extract_code(reply)
        outfile.write_text(code)
        print(f"  wrote {outfile.name} ({len(code)} chars)", flush=True)
        result = headless(slug)
        print(f"  headless: {json.dumps(result)[:300]}", flush=True)
        if result.get("ok"):
            print(f"\nSUCCESS in {rnd} round(s): {result.get('resolved')} @ frame {result.get('frame')}")
            return
        # feed the failure back
        messages += [
            {"role": "assistant", "content": f"```js\n{code}\n```"},
            {"role": "user", "content":
                f"That failed the headless sim test:\n{json.dumps(result)}\n\n"
                f"Fix the bug and output the COMPLETE corrected module as one ```js block. "
                f"Keep everything that worked; change only what the error requires."},
        ]
    print(f"\nFAILED after {rounds} rounds — last error above.")

if __name__ == "__main__":
    main()
