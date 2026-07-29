#!/usr/bin/env python3
"""Generate the images a generated game asked for, through the real image queue.

The game writes assets.json — {"images": [{id, file, w, h, prompt}]} — and renders each entry
with a fallback when the file is missing. This turns that manifest into actual PNGs using the
same path Maestro's skin stage uses: build_item_payload (which safety-screens the prompt) ->
enqueue on the `image` queue -> an image worker runs ComfyUI -> the result comes back inline.

  make_assets.py <game_dir> [--limit N]

Needs the control plane up, an image worker on the `image` queue, and ComfyUI on its target.
"""
import argparse
import base64
import json
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from db import queue_client                      # noqa: E402
from tools.comfyui_tools import build_item_payload  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("game_dir")
    ap.add_argument("--limit", type=int, default=0, help="only the first N images")
    a = ap.parse_args()

    game = Path(a.game_dir)
    manifest = json.loads((game / "assets.json").read_text())
    images = manifest.get("images") or manifest
    if a.limit:
        images = images[: a.limit]
    (game / "assets").mkdir(exist_ok=True)

    done = failed = blocked = 0
    for i, img in enumerate(images, 1):
        ident, prompt = img.get("id"), img.get("prompt") or ""
        dest = game / (img.get("file") or f"assets/{ident}.png")
        if dest.exists():
            print(f"  [{i}/{len(images)}] {ident}: already present")
            continue
        payload = build_item_payload(prompt)
        if payload is None:
            print(f"  [{i}/{len(images)}] {ident}: BLOCKED by prompt screen")
            blocked += 1
            continue
        job = queue_client.run_job("image", payload)
        if job.get("status") != "done":
            print(f"  [{i}/{len(images)}] {ident}: FAILED {str(job.get('error'))[:120]}")
            failed += 1
            continue
        result = job.get("result") or {}
        entries = result.get("images") or []
        if not entries:
            print(f"  [{i}/{len(images)}] {ident}: no image in result")
            failed += 1
            continue
        first = entries[0]
        dest.parent.mkdir(parents=True, exist_ok=True)
        # The workqueue router offloads big results to disk and leaves a path; small ones stay
        # inline as base64. Handle both rather than assuming which one we got.
        if first.get("b64"):
            dest.write_bytes(base64.b64decode(first["b64"]))
        elif first.get("file"):
            shutil.copyfile(first["file"], dest)
        else:
            print(f"  [{i}/{len(images)}] {ident}: result had neither b64 nor file")
            failed += 1
            continue
        print(f"  [{i}/{len(images)}] {ident}: {dest.name} ({dest.stat().st_size // 1024}K)")
        done += 1

    print(f"\ndone={done} failed={failed} blocked={blocked} of {len(images)}")
    return 0 if done else 1


if __name__ == "__main__":
    sys.exit(main())
