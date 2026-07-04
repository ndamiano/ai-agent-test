"""Deterministic slop scanner — mechanical defect detection on a run's authored scenes.

Model-independent: every check is a regex/count over nodes.json (+ story.json), each one the
signature of a defect actually observed in generated output. It finds review targets, it does
not grade — a hit is "a human should look here", not "this is bad".

    python -m eval.slop_scan <run_dir>            # human-readable report
    python -m eval.slop_scan <run_dir> --json     # machine-readable
"""
import json
import re
import sys
from collections import Counter
from pathlib import Path

# Observed line-level attractor shapes (the "significance machine"):
#   diagnosis  — a character explains the other's psychology to them
#   flip       — the "It's not X, it's Y" reframe that states the theme
#   summary    — closing a scene by saying what it meant
DIAGNOSIS_RES = [
    re.compile(r"\byou'?re (just |not )?(scared|afraid|terrified) (of|that|you)\b", re.I),
    re.compile(r"\byou think (that )?(if )?you can\b", re.I),
    re.compile(r"\byou'?re (playing|trying) to (win|pause|prove|control)\b", re.I),
]
FLIP_RE = re.compile(r"\bit'?s not (about )?(the )?\w+([ ,.]+it'?s| — it'?s)\b", re.I)
WASNT_JUST_RE = re.compile(r"\bit wasn'?t just \w+\. it was\b", re.I)
THEME_NOUNS = {"trust", "friendship", "bond", "chapter", "future", "dynamic", "journey",
               "memory", "memories", "connection", "moment", "moments"}
META_CHOICE_RE = re.compile(r"\b(next beat|the story|step back|press on|reflect)\b", re.I)
ID_RE = re.compile(r"\b(?:beat|ending|scene)_[a-z0-9_]+\b", re.I)
YEARISH_RE = re.compile(r"\b(?:19|20)\d\d\b|\b(?:\w+teen|twenty|\d+) years\b", re.I)


def _words(t):
    return re.findall(r"[A-Za-z'’]+", t.lower())


def scan(run_dir) -> dict:
    run_dir = Path(run_dir)
    nodes_doc = json.loads((run_dir / "nodes.json").read_text())
    story = {}
    if (run_dir / "story.json").exists():
        story = json.loads((run_dir / "story.json").read_text())
    chars = {}
    if (run_dir / "characters.json").exists():
        chars = json.loads((run_dir / "characters.json").read_text())
    places = {}
    if (run_dir / "places.json").exists():
        places = json.loads((run_dir / "places.json").read_text())
    # snake_case state ids: a flag/variable name spoken aloud is mechanics leaking into play
    state_ids = {f for f in places.get("flags", []) if isinstance(f, str) and "_" in f}
    for v in places.get("variables", []) or []:
        vid = v.get("id") if isinstance(v, dict) else v
        if isinstance(vid, str) and "_" in vid:
            state_ids.add(vid)
    for node in (nodes_doc.get("nodes") or {}).values():
        for ln in node.get("lines") or []:
            for eff in (ln or {}).get("effects") or []:
                if isinstance(eff, dict):
                    for k in ("set_flag", "clear_flag", "add_item", "remove_item"):
                        if isinstance(eff.get(k), str) and "_" in eff[k]:
                            state_ids.add(eff[k])
    char_names = {c["id"].lower() for c in chars.get("characters", []) if c.get("id")} | \
                 {(c.get("name") or "").lower() for c in chars.get("characters", [])} - {""}
    ending_stems = [e["id"].split("_", 1)[-1] for e in story.get("endings", [])
                    if isinstance(e, dict) and e.get("id") and "_" in e["id"]]

    findings = []
    opener_counts: Counter = Counter()
    line_stats: dict = {}
    timeline = []

    def flag(kind, node, line_ix, text, why):
        findings.append({"kind": kind, "node": node, "line": line_ix,
                         "text": text[:160], "why": why})

    for nid in nodes_doc.get("node_ids", []):
        node = (nodes_doc.get("nodes") or {}).get(nid) or {}
        lines = node.get("lines") or []
        spoken = [(i, ln) for i, ln in enumerate(lines)
                  if isinstance(ln, dict) and ln.get("speaker")]
        for i, ln in enumerate(lines):
            text = (ln or {}).get("text", "")
            speaker = (ln or {}).get("speaker")
            if not text:
                continue
            for m in YEARISH_RE.finditer(text):
                timeline.append({"node": nid, "line": i, "mention": m.group(0),
                                 "text": text[:120]})
            if ID_RE.search(text):
                flag("id_leak", nid, i, text, "a beat/ending/scene id appears in play text")
            if "(flag" in text.lower() or any(
                    re.search(rf"\b{re.escape(s)}\b", text) for s in state_ids):
                flag("mechanics_leak", nid, i, text,
                     "a flag/variable id or state note appears in play text")
            if speaker:
                st = line_stats.setdefault(speaker, {"lines": 0, "words": 0, "max": 0})
                n = len(_words(text))
                st["lines"] += 1
                st["words"] += n
                st["max"] = max(st["max"], n)
                if n > 60:
                    flag("run_on", nid, i, text,
                         f"{n}-word spoken line — nobody says this in one breath")
                first = text.split(" ", 1)[0].strip(",.;:!?")
                if first.lower() == speaker.lower() or first.lower() in char_names \
                        and first.lower() == (speaker or "").lower():
                    flag("name_glue", nid, i, text, "speaker's own name glued to the line")
                for rx in DIAGNOSIS_RES:
                    if rx.search(text):
                        flag("diagnosis", nid, i, text,
                             "explains the other character's psychology to them")
                        break
                if FLIP_RE.search(text) or WASNT_JUST_RE.search(text):
                    flag("theme_flip", nid, i, text, '"not X, it\'s Y" theme reframe')
                for stem in ending_stems:
                    if len(stem) >= 9 and re.search(rf"\b{re.escape(stem)}\b", text, re.I):
                        flag("spec_vocab", nid, i, text,
                             f"ending id stem '{stem}' spoken as dialogue")
                key = " ".join(_words(text)[:4])
                if len(key.split()) == 4:
                    opener_counts[key] += 1
        # scene closers: theme nouns in the last two spoken lines
        for i, ln in spoken[-2:]:
            hits = THEME_NOUNS & set(_words(ln.get("text", "")))
            if hits:
                flag("theme_closer", nid, i, ln["text"],
                     f"scene closes naming the theme ({', '.join(sorted(hits))})")
        end = node.get("end") or {}
        for j, ch in enumerate(end.get("choices") or []):
            ct = (ch or {}).get("text", "")
            if META_CHOICE_RE.search(ct) or ID_RE.search(ct):
                flag("meta_choice", nid, -1, ct,
                     "menu choice uses planning vocabulary, not in-world words")

    for key, n in opener_counts.items():
        if n > 3:
            findings.append({"kind": "circling", "node": None, "line": None, "text": key,
                             "why": f"the same 4-word opener appears {n}× across the script"})

    for sp, st in line_stats.items():
        st["mean"] = round(st["words"] / st["lines"], 1) if st["lines"] else 0

    return {"run": str(run_dir), "findings": findings, "line_stats": line_stats,
            "timeline_mentions": timeline,
            "counts": Counter(f["kind"] for f in findings)}


def report(result: dict) -> str:
    out = [f"slop_scan: {result['run']}"]
    counts = result["counts"]
    out.append("  " + (", ".join(f"{k}={v}" for k, v in sorted(counts.items()))
                       if counts else "no findings"))
    for f in result["findings"]:
        where = f"{f['node']}:{f['line']}" if f.get("node") else "(global)"
        out.append(f"  [{f['kind']}] {where} — {f['why']}")
        out.append(f"      {f['text']}")
    out.append("  line stats (per speaker): " + json.dumps(result["line_stats"]))
    if result["timeline_mentions"]:
        out.append("  timeline mentions (check the arithmetic by hand):")
        for t in result["timeline_mentions"]:
            out.append(f"    {t['node']}:{t['line']} {t['mention']!r} — {t['text']}")
    return "\n".join(out)


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if a != "--json"]
    if not args:
        sys.exit("usage: python -m eval.slop_scan <run_dir> [--json]")
    res = scan(args[0])
    res["counts"] = dict(res["counts"])
    print(json.dumps(res, indent=2) if "--json" in sys.argv else report(res))
