"""The play gate — press the game's documented inputs and hear which do nothing.

Runs after the error gate is clean: the game loads and does not throw, so what is left to detect
is a documented input whose promised, VISIBLE effect never appears. That stays inside the
BROKEN-not-bad guardrail the way an uncaught exception does: "pressing E uses the selected item"
is the game's own design speaking, and the note it becomes is satisfied by making that one input
work. Anything softer — depth, feel, pacing — is collected as JUDGMENT and written to the report
for a human; it never enters a fix note (a grade fed back as a fix note made nothing better —
docs/experiments.md, 2026-08-08).

One SESSION, one persistent page: play is stateful, so unlike the error gate's fresh-page pokes
every action lands on the same page, and the previous turn's after-screenshot is the next turn's
before. The model presses ONE input per turn, states the visible change it expects, and next turn
says met / unmet / unclear about its own prediction. Only what it watched fail may enter the
report, and the prompt biases to under-report: unclear is not broken, and a control that failed
once and worked later is not broken.

The gate is a BOUNDARY like the error gate: a session that cannot run logs and stands aside, and
losing the model mid-session keeps the turns already played. Round state in
runs/<id>/play_gate.json, the full report in runs/<id>/play_report.json.
"""

from __future__ import annotations

import base64
import json
import logging
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from llm_clients.connector import get_connector
from llm_clients.message_builder import MessageBuilder
from maestro.codegen.error_gate import VIEWPORT, _FENCE, _KEY_NAMES, _serve

logger = logging.getLogger(__name__)

MAX_TURNS = 10            # enough to start the game and try each documented input once
MAX_ROUNDS = 2            # a fix per fact, twice; a game still failing its inputs goes to a human
SETTLE_MS = 1500
IMAGE_WINDOW = 4
STATE_FILE = "play_gate.json"
REPORT_FILE = "play_report.json"
REQUEST_CHARS = 6000

_TURN = Path(__file__).parent / "prompts" / "play_turn.txt"
_VERDICT = Path(__file__).parent / "prompts" / "play_verdict.txt"
_NOTE = Path(__file__).parent / "prompts" / "play_fix_note.txt"


# ---------------------------------------------------------------- parsing

def _parse_json(text: str) -> Optional[dict]:
    try:
        data = json.loads(_FENCE.sub("", (text or "").strip()))
    except (ValueError, TypeError):
        return None
    return data if isinstance(data, dict) else None


def _parse_turn(text: str) -> Optional[Dict]:
    """One play turn as the model answered it, or None when it answered off-shape."""
    data = _parse_json(text)
    if data is None or _action_of(data.get("action")) is None:
        return None
    verdict = data.get("verdict")
    return {"verdict": verdict if verdict in ("met", "unmet", "unclear") else "unclear",
            "observed": str(data.get("observed") or ""),
            "action": data["action"],
            "expected": str(data.get("expected") or "")}


def _action_of(action) -> Optional[Tuple]:
    """An executable action: ("key", name), ("click", (x, y)) or ("hold", (name, seconds))."""
    if not isinstance(action, dict):
        return None
    if isinstance(action.get("click"), dict):
        try:
            x, y = int(action["click"]["x"]), int(action["click"]["y"])
        except (KeyError, TypeError, ValueError):
            return None
        if 0 <= x < VIEWPORT["width"] and 0 <= y < VIEWPORT["height"]:
            return ("click", (x, y))
        return None
    hold = isinstance(action.get("hold"), dict)
    src = action["hold"] if hold else action
    key = src.get("key")
    if not isinstance(key, str) or not key.strip():
        return None
    name = _KEY_NAMES.get(key.strip().lower(), key.strip())
    if len(name) == 1:
        name = name.lower() if name.isalpha() else name
    if hold:
        try:
            seconds = min(3.0, max(0.5, float(src.get("seconds", 1))))
        except (TypeError, ValueError):
            seconds = 1.0
        return ("hold", (name, seconds))
    return ("key", name)


def _parse_report(text: str, turns: List[Dict]) -> Dict:
    """The final report; an off-shape answer still yields a report with the turns played."""
    data = _parse_json(text) or {}
    broken = [f for f in (data.get("broken") or []) if isinstance(f, dict)
              and f.get("action") and f.get("expected")]
    judgment = data.get("judgment") if isinstance(data.get("judgment"), dict) else {}
    return {"broken": broken, "judgment": judgment, "turns": turns}


# ---------------------------------------------------------------- the session

def _elided(history: List[Dict]) -> List[Dict]:
    """The transcript with only the last IMAGE_WINDOW screenshots kept as pixels — an old frame's
    content lives on in the model's own observed/expected text, so the stub costs nothing."""
    user_idx = [i for i, m in enumerate(history) if m["role"] == "user"]
    keep = set(user_idx[-IMAGE_WINDOW:])
    out = []
    for i, m in enumerate(history):
        if m["role"] == "user" and i not in keep:
            texts = [p["text"] for p in m["content"] if p.get("type") == "text"]
            out.append({"role": "user",
                        "content": texts[0] + " [screenshot elided — see your answer below]"})
        else:
            out.append(m)
    return out


def _ask(system: str, history: List[Dict], max_tokens: int) -> Optional[str]:
    reply = get_connector().generate_with_tools(
        MessageBuilder(system).extend(_elided(history)).build(), [],
        max_tokens=max_tokens, reasoning="low")
    if reply.get("error"):
        logger.warning("play gate: llm turn failed (%s)", reply["error"])
        return None
    msg = (reply.get("choices") or [{}])[0].get("message", {}) or {}
    return msg.get("content") or None


def _frame(page, n: int) -> Dict:
    png = base64.b64encode(page.screenshot()).decode()
    return {"role": "user", "content": [
        {"type": "text", "text": f"Turn {n} — the game now:"},
        {"type": "image_url", "image_url": {"url": "data:image/png;base64," + png}}]}


def _act(page, action: Tuple) -> None:
    kind, what = action
    try:
        if kind == "click":
            page.mouse.click(*what)
        elif kind == "hold":
            name, seconds = what
            page.keyboard.down(name)
            page.wait_for_timeout(seconds * 1000)
            page.keyboard.up(name)
        else:
            page.keyboard.press(what)
    except Exception as e:
        logger.warning("play gate: could not press %s (%s)", what, e)


def play(game_dir: Path, request: str) -> Optional[Dict]:
    """One playtest session over the staged game; the report dict, or None when the session could
    not run at all. The page has NO network egress, same as the error gate's probe."""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        logger.warning("play gate: playwright not installed, session skipped")
        return None

    system = _TURN.read_text(encoding="utf-8").format(
        request=request[:REQUEST_CHARS], **VIEWPORT)
    history: List[Dict] = []
    turns: List[Dict] = []
    try:
        with _serve(game_dir) as base_url, sync_playwright() as pw:
            browser = pw.chromium.launch(
                proxy={"server": "http://127.0.0.1:9", "bypass": "127.0.0.1"},
                args=["--force-webrtc-ip-handling-policy=disable_non_proxied_udp"])
            page = browser.new_page(viewport=VIEWPORT)
            page.route("**/*", lambda route: route.continue_()
                       if route.request.url.startswith(f"{base_url}/") else route.abort())
            page.goto(f"{base_url}/index.html", timeout=15_000, wait_until="load")
            page.wait_for_timeout(SETTLE_MS)

            for n in range(1, MAX_TURNS + 1):
                history.append(_frame(page, n))
                answer = _ask(system, history, max_tokens=2500)
                turn = _parse_turn(answer) if answer else None
                if turn is None:
                    logger.warning("play gate: turn %d answered off-shape — ending session", n)
                    break
                history.append({"role": "assistant", "content": answer})
                turns.append(turn)
                _act(page, _action_of(turn["action"]))
                page.wait_for_timeout(SETTLE_MS)

            browser.close()
    except Exception as e:
        logger.warning("play gate: session could not run (%s) — build stands", e)
        return None

    if not turns:
        return None
    history.append({"role": "user", "content": _VERDICT.read_text(encoding="utf-8")})
    answer = _ask(system, history, max_tokens=3000)
    return _parse_report(answer or "", turns)


# ---------------------------------------------------------------- the note

def note_for_fact(fact: Dict) -> str:
    return _NOTE.read_text(encoding="utf-8").format(
        action=str(fact.get("action", "")).strip(),
        expected=str(fact.get("expected", "")).strip(),
        observed=str(fact.get("observed", "")).strip() or "(no visible change)").strip() + "\n"


# ---------------------------------------------------------------- the loop

def _state_path(run_dir) -> Path:
    return Path(run_dir) / STATE_FILE


def _load_state(run_dir) -> Dict:
    p = _state_path(run_dir)
    if p.exists():
        return json.loads(p.read_text(encoding="utf-8"))
    return {"rounds": 0, "last_fact": None}


def _save_state(run_dir, state: Dict) -> None:
    _state_path(run_dir).write_text(json.dumps(state, indent=2), encoding="utf-8")


def after_build(run_id: str) -> bool:
    """Playtest a build the error gate passed; a watched-failed input re-enters the fix machine
    with ONE fact's note. Returns whether a fix build was started. The judgment half of the
    report only ever lands in play_report.json — it is for the human.

    Same stops as the error gate: MAX_ROUNDS spent, or the same fact twice running."""
    from maestro.codegen import build_chain     # late import — build_chain imports this module
    from maestro.state import RunState
    from maestro.codegen.staging import game_dir
    from tools.execution_context import run_scope

    rs = RunState(run_id)
    spec = rs.read_spec() or {}
    state = _load_state(rs.run_dir)

    with run_scope(run_id):
        report = play(game_dir(rs.run_dir), str(spec.get("request") or ""))
    if report is None:
        return False
    (rs.run_dir / REPORT_FILE).write_text(
        json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")

    broken = report["broken"]
    if not broken:
        if state["rounds"]:
            logger.info("play gate: %s inputs clean after %d fix round(s)", run_id, state["rounds"])
        state["last_fact"] = None
        _save_state(rs.run_dir, state)
        return False

    fact = broken[0]
    key = f"{fact.get('action')}|{fact.get('expected')}"
    logger.info("play gate: %s reported %d broken input(s); first: %s",
                run_id, len(broken), str(fact.get("action"))[:80])
    if state["rounds"] >= MAX_ROUNDS:
        logger.warning("play gate: %s still failing inputs after %d rounds — leaving it to a human",
                       run_id, state["rounds"])
        return False
    if state["last_fact"] == key:
        logger.warning("play gate: %s repeated the same fact two rounds running — stopping", run_id)
        return False

    state["rounds"] += 1
    state["last_fact"] = key
    _save_state(rs.run_dir, state)
    build_chain.kickoff(run_id, kind="fix", note=note_for_fact(fact))
    return True
