"""The error gate — open the finished game in a headless browser and hear what throws.

Three rules make the fix loop converge (docs/experiments.md, "The error gate"). ONE error per
fix build — several errors in one note close none of them. Every error carries an ADDRESS — a
browser SyntaxError arrives with an empty stack, so node supplies the file and line, and a
cross-file declaration scan supplies the pair for a redeclaration neither tool can locate alone.
A parse error's note says to fix the WHOLE file — a parser reports one error per file, so a note
aimed at the line named fixes one instance per round and grinds.

An uncaught exception is one of the few signals that satisfies the BROKEN-not-bad guardrail:
`this._doIdle is not a function` can only be met by defining it. The gate does not simulate
play — it loads the page and pokes past a title screen, and whether the game PLAYS right stays
a human question. 404s are excluded deliberately: art lands after the code that draws it, and
a missing file is not a broken game (a crash while DRAWING the missing art still throws, and
that is caught).

The gate is a BOUNDARY like snapshots: a probe that cannot run is logged and the build stands —
losing the gate is never a reason to lose a game.
"""

from __future__ import annotations

import functools
import http.server
import json
import logging
import re
import socketserver
import subprocess
import tempfile
import threading
from pathlib import Path
from typing import Dict, List, Optional

from maestro.codegen.staging import RUNTIME_DIR

logger = logging.getLogger(__name__)

MAX_ROUNDS = 8            # measured convergence was <= 6 rounds on the worst cell
PROBE_SECONDS = 6.0       # settle time after load + pokes; boot errors land well inside it
STATE_FILE = "error_gate.json"

VENDORED = {p.name for p in (RUNTIME_DIR / "vendor").glob("*.js")}

_NOTE = (Path(__file__).parent / "prompts" / "error_gate_note.txt")

_SYNTAXY = re.compile(r"SyntaxError|redeclaration|already been declared|Unexpected (?:token|identifier|string|number|end of input)|missing [)}\]] after", re.IGNORECASE)
_REDECL = re.compile(r"redeclaration of (?:const|let|var|class|function)?\s*['\"]?(\w+)"
                     r"|Identifier '(\w+)' has already been declared")
_INLINE_SCRIPT = re.compile(r"<script(?![^>]*\bsrc=)[^>]*>(.*?)</script>", re.IGNORECASE | re.DOTALL)


# ---------------------------------------------------------------- the probe

def probe(game_dir: Path) -> List[Dict[str, str]]:
    """Serve the game folder, load it headless, and return the uncaught errors in arrival order.

    Each error is {"message": ..., "stack": ...}. The page gets a click at the viewport centre
    (where a canvas-drawn PLAY button sits) and the two keys any title screen answers to (Enter,
    Space) — enough to get past "press to start", deliberately no more. A script the page asked
    for and did not get is an error too: a module import that 404s stops the whole module graph
    without throwing, so nothing else would ever report it. An environment with no browser
    answers [] and logs why, per the boundary rule. The page has NO network egress: every request
    that is not the game's own ephemeral server is aborted and logged.
    """
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        logger.warning("error gate: playwright not installed, probe skipped")
        return []

    errors: List[Dict[str, str]] = []
    blocked: List[str] = []
    with _serve(game_dir) as base_url:
        try:
            with sync_playwright() as pw:
                # The probe runs the game's own JS on the control-plane box, so the game may
                # reach ONLY the ephemeral server. The dead proxy is the floor — route
                # interception never sees websockets, a proxied browser sends everything
                # (DNS included) through it — and the route is the report. The WebRTC flag
                # closes the one channel a proxy does not carry.
                browser = pw.chromium.launch(
                    proxy={"server": "http://127.0.0.1:9", "bypass": "127.0.0.1"},
                    args=["--force-webrtc-ip-handling-policy=disable_non_proxied_udp"])
                page = browser.new_page()

                def _no_egress(route):
                    url = route.request.url
                    if url.startswith(f"{base_url}/"):
                        route.continue_()
                    else:
                        blocked.append(url)
                        route.abort()

                page.route("**/*", _no_egress)
                page.on("pageerror", lambda e: errors.append(
                    {"message": str(e), "stack": getattr(e, "stack", "") or ""}))

                def _missing_script(response):
                    req = response.request
                    if response.status < 400 or req.resource_type != "script":
                        return
                    path = req.url[len(base_url) + 1:]
                    referrer = (req.headers.get("referer") or "")[len(base_url) + 1:]
                    who = f" ({referrer} asked for it)" if referrer and referrer != "index.html" else ""
                    errors.append({"message": f"the page asked for {path} and it does not exist "
                                              f"(HTTP {response.status}){who} — the path in the "
                                              f"script tag or import that names it is wrong",
                                   "stack": ""})

                page.on("response", _missing_script)
                try:
                    page.goto(f"{base_url}/index.html", timeout=15_000, wait_until="load")
                except Exception as e:
                    # A page that cannot finish loading (script hangs the parser, endless loop
                    # in module init) is itself the finding when nothing threw first.
                    if not errors:
                        errors.append({"message": f"page did not finish loading: {e}", "stack": ""})
                page.wait_for_timeout(PROBE_SECONDS * 500)
                size = page.viewport_size or {"width": 1280, "height": 720}
                for poke in (lambda: page.mouse.click(size["width"] // 2, size["height"] // 2),
                             lambda: page.keyboard.press("Enter"),
                             lambda: page.keyboard.press("Space")):
                    try:
                        poke()
                    except Exception:
                        pass
                page.wait_for_timeout(PROBE_SECONDS * 500)
                browser.close()
        except Exception as e:
            logger.warning("error gate: probe could not run (%s) — build stands", e)
            return []
    if blocked:
        logger.warning("error gate: %s: blocked %d external request(s): %s",
                       game_dir, len(blocked), ", ".join(sorted(set(blocked))[:5]))
    return _dedup(errors)


class _Quiet(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *args):  # a probe's 404s are expected, not log noise
        pass


class _serve:
    """An ephemeral static server for one probe: `with _serve(dir) as url:`."""

    def __init__(self, root: Path):
        self.root = root

    def __enter__(self) -> str:
        handler = functools.partial(_Quiet, directory=str(self.root))
        self.httpd = socketserver.TCPServer(("127.0.0.1", 0), handler)
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        return f"http://127.0.0.1:{self.httpd.server_address[1]}"

    def __exit__(self, *exc):
        self.httpd.shutdown()
        self.httpd.server_close()
        return False


def _dedup(errors: List[Dict[str, str]]) -> List[Dict[str, str]]:
    """A render loop re-throws the same error every frame; the gate wants each once, first-seen
    order kept (the first error is usually the cause and the rest its wake)."""
    seen, out = set(), []
    for e in errors:
        key = e["message"]
        if key not in seen:
            seen.add(key)
            out.append(e)
    return out


# ---------------------------------------------------------------- the address

def address(error: Dict[str, str], game_dir: Path) -> str:
    """Where the error lives, as text for the note. A runtime error carries its own stack; a
    SyntaxError reaches the browser with an empty one, so node re-parses the authored files to
    name the file and line, and a redeclaration adds every declaration site of the identifier."""
    lines: List[str] = []
    stack = (error.get("stack") or "").strip()
    if stack and not _SYNTAXY.search(error["message"]):
        lines.append(stack.splitlines()[0] if len(stack.splitlines()) == 1 else stack)
    else:
        lines.extend(_parse_addresses(game_dir))
        redecl = _REDECL.search(error["message"])
        if redecl:
            name = redecl.group(1) or redecl.group(2)
            lines.extend(_declaration_sites(name, game_dir))
    return "\n".join(lines)


def _authored_js(game_dir: Path) -> List[Path]:
    """Every .js the model wrote, wherever it put it — games keep code under game/ or js/ as
    often as at the root. The vendored renderer and lib/ beside the game are not the model's."""
    return [p for p in sorted(game_dir.rglob("*.js"))
            if not (p.parent == game_dir and p.name in VENDORED)
            and "lib" not in p.relative_to(game_dir).parts[:-1]]


def _parse_addresses(game_dir: Path) -> List[str]:
    """file:line for every authored file node cannot parse, inline scripts included. A file is
    tried as a module first (authored games import three), then as a script; only a file both
    parsers refuse is broken."""
    out = []
    for path in _authored_js(game_dir):
        err = _node_check(path.read_text(encoding="utf-8", errors="replace"))
        if err:
            out.append(f"{path.relative_to(game_dir)}: {err}")
    entry = game_dir / "index.html"
    if entry.exists():
        for i, script in enumerate(_INLINE_SCRIPT.findall(
                entry.read_text(encoding="utf-8", errors="replace"))):
            err = _node_check(script)
            if err:
                out.append(f"index.html inline script #{i + 1}: {err}")
    return out


def _node_check(source: str) -> Optional[str]:
    """The module parser's first error line, or None if either parser accepts the source. Games
    are modules, so its line is the one reported; the script pass only decides whether a file
    that is not a module is fine as a script."""
    first_err = None
    # .cjs, not .js: node 22 detects `export` in a .js file and answers a CommonJS parse failure
    # with "retry as a module" — reported as a pass, so every module file checked as .js is clean.
    for suffix in (".mjs", ".cjs"):
        with tempfile.NamedTemporaryFile("w", suffix=suffix, delete=False) as f:
            f.write(source)
            tmp = f.name
        try:
            res = subprocess.run(["node", "--check", tmp], capture_output=True, text=True,
                                 timeout=20)
        except (OSError, subprocess.TimeoutExpired) as e:
            logger.warning("error gate: node --check unavailable (%s)", e)
            return None
        finally:
            Path(tmp).unlink(missing_ok=True)
        if res.returncode == 0:
            return None
        if first_err is None:
            first = next((ln for ln in res.stderr.splitlines() if ln.strip()), res.stderr)
            first_err = re.sub(re.escape(tmp), "", first).strip(" :")
    return first_err


def _declaration_sites(name: str, game_dir: Path) -> List[str]:
    pattern = re.compile(rf"\b(?:const|let|var|function|class)\s+{re.escape(name)}\b")
    out = []
    for path in _authored_js(game_dir) + [game_dir / "index.html"]:
        if not path.exists():
            continue
        for n, line in enumerate(path.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
            if pattern.search(line):
                out.append(f"{path.relative_to(game_dir)}:{n}: {line.strip()}")
    return out


# ---------------------------------------------------------------- the note

def note_for(error: Dict[str, str], game_dir: Path) -> str:
    template = _NOTE.read_text(encoding="utf-8")
    where = address(error, game_dir)
    parse_line = ""
    if _SYNTAXY.search(error["message"]):
        parse_line = ("A file stops parsing at its first error, so fix every occurrence of the "
                      "problem in that file, not only the line named.")
    return template.format(message=error["message"].strip(),
                           address=where or "(no location available — find it by reading the files)",
                           parse_note=parse_line).strip() + "\n"


# ---------------------------------------------------------------- the loop

def _state_path(run_dir) -> Path:
    return Path(run_dir) / STATE_FILE

def _load_state(run_dir) -> Dict:
    p = _state_path(run_dir)
    if p.exists():
        return json.loads(p.read_text(encoding="utf-8"))
    return {"rounds": 0, "last_message": None}

def _save_state(run_dir, state: Dict) -> None:
    _state_path(run_dir).write_text(json.dumps(state, indent=2), encoding="utf-8")


def after_build(run_id: str) -> bool:
    """Probe a just-finalized playable build; a thrown error re-enters the fix machine with ONE
    error's note. Runs outside the run's advance lock (kickoff blocks until it is free).
    Returns whether a fix build was started — a caller with its own next step (a stage advance)
    must stand down while the gate is still converging.

    Two stops besides a clean probe: MAX_ROUNDS spent (a game the gate cannot converge belongs
    to a human), and the same first error twice running (a fix that changed nothing will not
    change next round either)."""
    from maestro.codegen import build_chain     # late import — build_chain imports this module
    from maestro.state import RunState
    from maestro.codegen.staging import game_dir as staged_game_dir

    rs = RunState(run_id)
    gdir = staged_game_dir(rs.run_dir)
    state = _load_state(rs.run_dir)

    errors = probe(gdir)
    if not errors:
        if state["rounds"]:
            logger.info("error gate: %s clean after %d fix round(s)", run_id, state["rounds"])
        state["last_message"] = None
        _save_state(rs.run_dir, state)
        return False

    first = errors[0]
    logger.info("error gate: %s threw %d error(s); first: %s", run_id, len(errors),
                first["message"].splitlines()[0][:200])
    if state["rounds"] >= MAX_ROUNDS:
        logger.warning("error gate: %s still throwing after %d rounds — leaving it to a human",
                       run_id, state["rounds"])
        return False
    if state["last_message"] == first["message"]:
        logger.warning("error gate: %s repeated the same error two rounds running — stopping",
                       run_id)
        return False

    state["rounds"] += 1
    state["last_message"] = first["message"]
    _save_state(rs.run_dir, state)
    build_chain.kickoff(run_id, kind="fix", note=note_for(first, gdir))
    return True
