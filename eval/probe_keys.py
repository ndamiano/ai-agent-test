#!/usr/bin/env python3
"""Open a generated game, press the keys a player would, and report what breaks.

The harnesses' own verifier only CLICKS. Three of the four battery genres are keyboard-driven,
so a platformer that dies the instant you press Enter is recorded as "0 errors" - measured: the
ninfer platformer reported 0 console errors and crashed with `player is undefined` on its first
keypress, because nothing ever reached the start path.

This only observes. It never edits a game.

  probe_keys.py <game_dir> [<game_dir> ...]

Writes _shots/03_keys.png next to the game and prints one JSON line per directory.
"""
import json
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

GRID = Path.home() / "output" / "grid"
GRID_PORT = 8124

START_KEYS = ["Enter", "Space"]


def page_url(path):
    """Serve over http when we can. A 3D game uses `<script type="module">`, which the browser
    refuses to load from a file:// origin ('blocked by CORS policy'), so a file:// probe reports
    a broken page for a game that runs fine in the grid."""
    p = Path(path).resolve()
    try:
        rel = p.relative_to(GRID)
    except ValueError:
        return f"file://{p}/index.html"
    return f"http://localhost:{GRID_PORT}/{rel}/index.html"
PLAY_KEYS = ["ArrowRight", "ArrowLeft", "ArrowUp", "ArrowDown",
             "w", "a", "s", "d", "Space", "e", "r"]


def probe(page, path):
    errors, seen = [], set()

    def note(kind, text):
        line = f"{kind}: {text}"[:300]
        if line not in seen:
            seen.add(line)
            errors.append(line)

    page.on("pageerror", lambda e: note("pageerror", str(e)))
    page.on("console", lambda m: note("console", m.text) if m.type == "error" else None)

    page.goto(page_url(path))
    page.wait_for_timeout(1200)
    at_load = list(errors)

    # A title screen usually wants Enter or Space before anything else responds.
    for k in START_KEYS:
        page.keyboard.press(k)
        page.wait_for_timeout(400)
    after_start = [e for e in errors if e not in at_load]

    for k in PLAY_KEYS:
        page.keyboard.down(k)
        page.wait_for_timeout(120)
        page.keyboard.up(k)
    page.wait_for_timeout(600)
    after_play = [e for e in errors if e not in at_load and e not in after_start]

    shots = Path(path) / "_shots"
    shots.mkdir(exist_ok=True)
    page.screenshot(path=str(shots / "03_keys.png"))
    body = (page.inner_text("body") or "").strip().replace("\n", " | ")[:200]
    return {"dir": str(path), "on_load": at_load, "on_start": after_start,
            "on_play": after_play, "body": body}


def main():
    out = []
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        for d in sys.argv[1:]:
            p = Path(d).resolve()
            if not (p / "index.html").exists():
                print(json.dumps({"dir": str(p), "error": "no index.html"}))
                continue
            page = browser.new_page(viewport={"width": 1280, "height": 800})
            try:
                r = probe(page, p)
            except Exception as e:
                r = {"dir": str(p), "error": str(e)[:200]}
            page.close()
            print(json.dumps(r))
            out.append(r)
        browser.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
