#!/usr/bin/env python3
"""Quick discrimination test for the judge.
Scores a known-good premise and a deliberately bad one, prints side-by-side.
Requires fixtures: python eval/cli.py capture renpy --brief renpy_romance
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

_good_path = FIXTURES_DIR / "renpy/renpy_romance/premise.json"
if not _good_path.exists():
    sys.exit(f"Missing fixture {_good_path}.\nRun: python eval/cli.py capture renpy --brief renpy_romance")
GOOD = json.loads(_good_path.read_text())

BAD = {
    "premise": "two people meet and talk and stuff happens",
    "central_question": "will things work out?",
    "protagonist_id": "person_a",
    "setting": {"name": "a place", "physical_description": "somewhere", "atmosphere": "normal"},
    "tone_directives": [{"adjective": "fine", "explanation": "it is fine"}],
    "characters": [
        {"id": "person_a", "name": "Person A", "identity": "a person", "situation": "exists",
         "charge": "wants things", "voice": "talks normally", "appearance": "a person"},
        {"id": "person_b", "name": "Person B", "identity": "another person", "situation": "also exists",
         "charge": "none really", "voice": "talks normally too", "appearance": "another person"},
    ],
}

rubric = json.loads((RUBRICS_DIR / "premise.json").read_text())
judge = Judge()

print("Scoring GOOD premise...")
good_result = judge.score("premise", GOOD, rubric)
print("Scoring BAD premise...")
bad_result  = judge.score("premise", BAD,  rubric)

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
