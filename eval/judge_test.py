#!/usr/bin/env python3
"""Quick discrimination test for the judge.
Scores a known-good story and a deliberately bad story, prints side-by-side.
Usage: python eval/judge_test.py
"""
import sys
from pathlib import Path

_root = Path(__file__).resolve().parent.parent
for _p in [str(_root), str(_root / "src")]:
    if _p not in sys.path:
        sys.path.insert(0, _p)

import json
from eval.judge import Judge
from eval._paths import RUBRICS_DIR, FIXTURES_DIR

GOOD = json.loads((FIXTURES_DIR / "renpy/renpy_romance/story.json").read_text())

BAD = {
    "arc": "character does things and stuff happens",
    "premise": "two people meet and talk",
    "story_beats": [
        {"beat_id": "beat_01", "label": "scene 1", "description": "they talk", "location_type": "a place"},
        {"beat_id": "beat_02", "label": "scene 2", "description": "more talking", "location_type": "another place"},
        {"beat_id": "beat_03", "label": "scene 3", "description": "the end", "location_type": "somewhere"},
    ],
    "location_needs": [
        {"need": "a location", "suggested_name": "place"},
    ],
}

rubric = json.loads((RUBRICS_DIR / "renpy_story.json").read_text())
judge = Judge()

print("Scoring GOOD story...")
good_result = judge.score("story", GOOD, rubric)
print("Scoring BAD story...")
bad_result  = judge.score("story", BAD,  rubric)

def fmt(result, label):
    if not result:
        print(f"{label}: FAILED (judge returned None)")
        return
    print(f"\n{'─'*40}")
    print(f"{label}  overall={result['overall']:.2f}")
    for name, s in result["scores"].items():
        if isinstance(s, dict):
            print(f"  {name:<25} {s.get('score', '?'):.1f}  {s.get('reasoning', '')}")

fmt(good_result, "GOOD")
fmt(bad_result,  "BAD ")

if good_result and bad_result:
    delta = good_result["overall"] - bad_result["overall"]
    print(f"\nDelta: {delta:+.1f}  ({'discriminating ✓' if delta > 15 else 'weak discrimination — judge may be inflating ✗'})")
