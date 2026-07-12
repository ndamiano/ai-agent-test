#!/usr/bin/env python3
"""Feed probe/headless violations back to the model to fix an existing game.
   python3 fix_round.py pacman [rounds]"""
import json, subprocess, sys
from pathlib import Path
from codegen_test import chat, extract_code, headless, RT

def probe(slug):
    p = subprocess.run(["node", "probe.mjs", f"games/{slug}.js"], cwd=RT,
                       capture_output=True, text=True, timeout=60)
    try: return json.loads(p.stdout)
    except Exception: return {"ok": False, "violations": [{"kind": "runner", "detail": p.stderr[-400:]}]}

def main():
    slug = sys.argv[1] if len(sys.argv) > 1 else "pacman"
    rounds = int(sys.argv[2]) if len(sys.argv) > 2 else 3
    model = "Qwen3-Coder-30B-A3B-Instruct-Q4_K_M"
    api = (RT / "kit_api.md").read_text()
    outfile = RT / "games" / f"{slug}.js"

    for rnd in range(1, rounds + 1):
        hl = headless(slug)
        pr = probe(slug)
        print(f"== round {rnd}: headless={hl.get('ok')} probe={pr.get('ok')} ==", flush=True)
        if hl.get("ok") and pr.get("ok"):
            print(f"CLEAN in {rnd-1} fix round(s): headless ran + probe passed."); return
        fails = []
        if not hl.get("ok"): fails.append(f"HEADLESS CRASH: {json.dumps(hl)}")
        for v in pr.get("violations", []): fails.append(f"CORRECTNESS [{v['kind']}]: {v['detail']}")
        print("  feeding back:", " || ".join(f[:120] for f in fails), flush=True)
        code = outfile.read_text()
        msg = [
            {"role": "system", "content": "You fix bugs in a JS game module built on the given kit. "
             "Output ONLY the complete corrected module as one ```js block, no prose."},
            {"role": "user", "content":
                f"# KIT API\n{api}\n\n# CURRENT MODULE\n```js\n{code}\n```\n\n"
                f"# FAILURES TO FIX\n" + "\n".join(f"- {f}" for f in fails) +
                f"\n\nFix ALL of these. Keep everything that works. For wall collision, check the "
                f"target cell with tilemap.solidAt BEFORE moving an entity into it and stop it at the "
                f"corridor, never place it on a wall tile. Output the full corrected ```js module."},
        ]
        reply = chat(model, msg, max_tokens=16000)
        outfile.write_text(extract_code(reply))
        print(f"  rewrote {outfile.name}", flush=True)
    hl, pr = headless(slug), probe(slug)
    print(f"FINAL: headless={hl.get('ok')} probe={pr.get('ok')} — {json.dumps(pr)[:300]}")

if __name__ == "__main__":
    main()
