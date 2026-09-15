"""The functions a build's program calls. Every path stays inside the game folder, and every failure
comes back to the model as text."""

import json
import os
import re
import signal
import subprocess
import sys
from pathlib import Path
from typing import Annotated, Any, List, Literal, Optional, Union

from pydantic import AfterValidator, ValidationError, validate_call

from maestro.codegen import file_state
from maestro.codegen.assets import DEFAULT_KIND, KINDS, MEDIA_ID, read_manifest, request_media
from maestro.codegen.staging import RUNTIME_DIR, game_dir

_VENDOR_FILES = {p.name for p in (RUNTIME_DIR / "vendor").glob("*.js")}

_REF = re.compile(r"""(?:from|import)\s*\(?\s*['"]([^'"\s]+)['"]|(?:src|href)\s*=\s*["']([^"'\s]+)["']""")
_REF_SKIP = ("http://", "https://", "data:", "blob:", "//", "#", "mailto:")

MAX_READ_CHARS = 8_000_000

DESIGN = "design/design.md"
_CHECKBOX = re.compile(r"^[ \t]*[-*][ \t]+\[( |x|X)\][ \t]*(.*?)[ \t]*$", re.M)


_ESCAPES = {"\\r\\n": "\n", "\\n": "\n", "\\t": "\t", "\\\"": "\"", "\\'": "'"}


def _unescaped(text: str) -> str:
    for k, v in _ESCAPES.items():
        text = text.replace(k, v)
    return text


DOUBLE_ESCAPED = ("Your text is escaped twice — it carries backslash sequences (\\n, \\\") where "
                  "the file has a real newline or quote. Send the characters themselves, escaped "
                  "once for JSON.")


def _nonblank(what: str, how: str):
    def check(v: str) -> str:
        if not v.strip():
            raise ValueError(f"{what} is required: {how}")
        return v
    return check


def _asset_id(v: str) -> str:
    if not MEDIA_ID.match(v):
        raise ValueError("id must be 1-64 characters of letters, digits, - or _")
    return v


GamePath = Annotated[str, AfterValidator(_nonblank("path", "the file to act on"))]
AssetId = Annotated[str, AfterValidator(_asset_id)]
Subject = Annotated[str, AfterValidator(_nonblank("subject", "describe what to draw"))]
Style = Annotated[str, AfterValidator(_nonblank("style", "the game's one style phrase"))]
Description = Annotated[str, AfterValidator(_nonblank("description", "the world to build"))]
Kind = Literal[KINDS]


def _safe(root: Path, path: str) -> Path:
    p = (root / path).resolve()
    if not str(p).startswith(str(root.resolve()) + os.sep):
        raise ValueError(f"path escapes the project directory: {path!r}")
    return p


def _dead_refs(path: Path, text: str, root: Path, pending) -> list:
    """Local paths this file names that resolve to nothing; art still queued is not missing."""
    out = []
    for a, b in _REF.findall(text):
        ref = (a or b).split("?")[0].split("#")[0]
        if not ref or ref.startswith(_REF_SKIP) or "${" in ref or "." not in Path(ref).name:
            continue
        target = (path.parent / ref).resolve()
        rel = os.path.relpath(target, root.resolve())
        if rel not in pending and not target.exists():
            out.append(ref)
    return out


def _row_key(text: str) -> str:
    text = re.sub(r"^[ \t]*[-*][ \t]+\[[ xX]\]", "", text)
    return " ".join(text.split()).lower()


def _argument_problem(name: str, kw: dict, e: ValidationError) -> str:
    """What the call got wrong, in one sentence; a missing argument also lists what was sent."""
    missing = [str(err["loc"][0]) for err in e.errors()
               if err["type"] in ("missing", "missing_argument") and err["loc"]]
    if missing:
        sent = ", ".join(sorted(kw)) or "no arguments"
        names = ", ".join(repr(m) for m in missing)
        word = "argument" if len(missing) == 1 else "arguments"
        return f"{name} needs the {word} {names}; this call sent {sent}."
    return f"{name}: " + "; ".join(_said(err) for err in e.errors())


def _said(err: dict) -> str:
    if err["type"] == "value_error":
        return err["msg"].removeprefix("Value error, ")
    loc = ".".join(str(x) for x in err["loc"]) or "arguments"
    return f"{loc}: {err['msg']} (got {err['input']!r})"


def build_tools(state, build_id: str) -> dict:
    root = game_dir(state.run_dir)

    def list_files(**_) -> dict:
        if not root.exists():
            return {"ok": True, "files": []}
        out = []
        for p in sorted(root.rglob("*")):
            if p.is_file() and not p.name.startswith("_"):
                out.append({"path": str(p.relative_to(root)), "bytes": p.stat().st_size})
        return {"ok": True, "files": out}

    @validate_call
    def read_file(path: GamePath, offset: Optional[int] = None, lines: Optional[int] = None,
                  **_) -> dict:
        """Whole lines from 1-based `offset`, at most `lines` of them."""
        p = _safe(root, path)
        if not p.exists():
            rel = str(p.relative_to(root.resolve()))
            entry = next((e for e in read_manifest(state.run_dir) if e.get("file") == rel), None)
            if entry:
                return {"ok": True, "pending": True,
                        "note": f"{path} is queued for rendering — generate_media id "
                                f"\"{entry['id']}\" — and will appear at exactly this path when "
                                "the render lands. Keep loading it by this path in your code, and "
                                "do not request it again."}
            return {"ok": False, "error": f"no such file: {path}"}
        if p.name in _VENDOR_FILES and p.parent == root:
            return {"ok": False,
                    "error": f"{path} is the vendored renderer — library code shipped with every "
                             "game, not this game's own. Import it (e.g. `import * as THREE from "
                             "'./three.module.js'`) and use the standard three.js API; nothing "
                             "inside it needs reading."}
        if b"\x00" in p.read_bytes()[:8192]:
            return {"ok": False,
                    "error": f"{path} is a binary file ({p.stat().st_size} bytes), not text — "
                             "there is nothing in it to read. The game loads it at runtime by "
                             "its path; nothing about its contents is needed to write that code."}
        want = int(lines) if lines else None
        lines = p.read_text(encoding="utf-8", errors="replace").splitlines(keepends=True)
        total = len(lines)
        start = max(int(offset or 1), 1) - 1
        if start and start >= total:
            return {"ok": False,
                    "error": f"offset {start + 1} is past the end of {path}: it has {total} lines."}
        window, chars = [], 0
        for line in lines[start:start + want if want else total]:
            if window and chars + len(line) > MAX_READ_CHARS:
                break
            window.append(line)
            chars += len(line)
        content = "".join(window)
        end = start + len(window)
        if len(content) > MAX_READ_CHARS:
            content = content[:MAX_READ_CHARS] + (
                f"\n\n[line {end} is longer than one read and was cut here, mid-line — text ending "
                f"at that cut is not what the file says, so do not use it as an edit anchor.]")
        elif end < total and not (want and end - start >= want):
            content += (
                f"\n\n[showed lines {start + 1}-{end} of {total}; read the rest with offset "
                f"{end + 1}.]")
        file_state.record_read(state.run_dir, str(p.relative_to(root.resolve())), p.read_bytes())
        return {"ok": True, "path": path, "content": content, "lines": f"{start + 1}-{end}/{total}"}

    @validate_call
    def write_file(path: GamePath, content: str, **_) -> dict:
        p = _safe(root, path)
        if "\n" not in content and "\\n" in content:
            return {"ok": False, "error": DOUBLE_ESCAPED}
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
        return {"ok": True, "path": path, "chars": len(content)}

    @validate_call
    def edit_file(path: GamePath, old_text: str, new_text: str, **_) -> dict:
        p = _safe(root, path)
        if not p.exists():
            return {"ok": False, "error": f"no such file: {path}"}
        old, new = old_text, new_text
        if old == new:
            return {"ok": False, "error": "old_text and new_text are identical, so this edit "
                                          "changes nothing. If the file already reads the way you "
                                          "want, move on."}
        body = p.read_text(encoding="utf-8")
        n = body.count(old)
        if n == 0:
            if _unescaped(old) != old and body.count(_unescaped(old)) > 0:
                return {"ok": False, "error": DOUBLE_ESCAPED}
            return {"ok": False, "error": "old_text was not found in the file. Read the file and "
                                          "copy the exact text, including whitespace."}
        if n > 1:
            return {"ok": False, "error": f"old_text appears {n} times. Include more surrounding "
                                          "text to make it unique."}
        p.write_text(body.replace(old, new), encoding="utf-8")
        return {"ok": True, "path": path, "chars": len(new)}

    @validate_call
    def generate_media(id: AssetId, subject: Subject, style: Style, kind: Optional[Kind] = None,
                       details: Any = None, **_) -> dict:
        return request_media(state.run_id, state.run_dir, build_id, id, subject, style,
                             kind or DEFAULT_KIND, details)

    @validate_call
    def compose_world(description: Description, seed: Optional[int] = None, **_) -> dict:
        from maestro.worldgen.compose import coming, compose
        if coming(state.run_dir):
            return {"ok": False,
                    "error": "this game's world is already on its way to world/world.json — "
                             "load it from there. Its size and regions are on the world the "
                             "loader hands back, read in the game at runtime."}
        return compose(root, state.run_dir, state.run_id, build_id, description,
                       int(seed) if seed is not None else None)

    @validate_call
    def check_syntax(paths: Optional[Union[str, List[str]]] = None, **_) -> dict:
        """Does the game's JavaScript parse, and does every file it loads exist?"""
        from maestro.codegen.error_gate import _node_message
        if paths is None:
            paths = [str(p.relative_to(root)) for p in sorted(root.rglob("*.js"))
                     if p.name not in _VENDOR_FILES and p.parent != root / "lib"]
        elif isinstance(paths, str):
            paths = [paths]
        entry = root / "index.html"
        if entry.exists() and "index.html" not in paths:
            paths = [*paths, "index.html"]
        pending = {e["file"] for e in read_manifest(state.run_dir) if e.get("file")}
        out = {}
        for rel in paths:
            p = _safe(root, rel)
            if not p.exists():
                out[rel] = f"no such file: {rel}"
                continue
            text = p.read_text(encoding="utf-8", errors="replace")
            msg = "" if p.suffix == ".html" else (_node_message(text) or "")
            dead = _dead_refs(p, text, root, pending)
            if dead:
                msg = (msg + " " if msg else "") + "loads a file that is not there: " + ", ".join(dead)
            out[rel] = msg or "OK"
        return {"ok": True, "checked": out}

    def play(js: str, seconds: float = 20, **_) -> dict:
        seconds = max(1.0, min(float(seconds), 60.0))
        env = {**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parents[2])}
        proc = subprocess.Popen([sys.executable, "-m", "maestro.codegen.play"],
                                stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, env=env, start_new_session=True)
        try:
            out, err = proc.communicate(json.dumps({"root": str(root), "js": js,
                                                    "seconds": seconds}).encode(),
                                        timeout=seconds + 25)
        except subprocess.TimeoutExpired:
            os.killpg(proc.pid, signal.SIGKILL)
            proc.communicate()
            return {"ok": False, "error": f"the page stopped responding; play was killed after "
                    f"{int(seconds)} s — a script or a game loop that never yields"}
        if proc.returncode != 0 or not out:
            return {"ok": False, "error": "play could not run: " + err.decode(errors="replace")[-400:]}
        return json.loads(out)

    @validate_call
    def check_off(rows: Union[str, List[str]], **_) -> dict:
        p = root / DESIGN
        if not p.exists():
            return {"ok": False, "error": f"no such file: {DESIGN}"}
        body = p.read_text(encoding="utf-8")
        boxes = list(_CHECKBOX.finditer(body))
        ticked, unmatched, at = [], [], set()
        for row in ([rows] if isinstance(rows, str) else rows):
            want = _row_key(row)
            exact = [m for m in boxes if _row_key(m.group(2)) == want]
            open_ = [m for m in exact if m.group(1) == " " and m.start() not in at] or exact
            hits = open_ or [m for m in boxes if want and want in _row_key(m.group(2))]
            if not want or not hits or (not exact and len(hits) > 1):
                unmatched.append(row)
                continue
            m = hits[0]
            at.add(m.start())
            ticked.append(m.group(2))
        for m in sorted(boxes, key=lambda m: -m.start()):
            if m.start() in at:
                body = body[:m.start(1)] + "x" + body[m.end(1):]
        p.write_text(body, encoding="utf-8")
        unchecked = [m.group(2) for m in _CHECKBOX.finditer(body) if m.group(1) == " "]
        out = {"ok": True, "ticked": ticked, "unchecked": unchecked}
        if unmatched:
            out["unmatched"] = unmatched
            out["note"] = ("unmatched rows were left alone: pass each row's text as it reads in "
                           f"{DESIGN}.")
        return out

    def done(summary=None, **_) -> dict:
        return {"ok": True, "summary": str(summary or "")}

    def _reported(fn):
        """Anything the call raises comes back as an error the model can act on."""
        def call(**kw):
            try:
                return fn(**kw)
            except ValidationError as e:
                return {"ok": False, "error": _argument_problem(fn.__name__, kw, e)}
            except Exception as e:
                return {"ok": False, "error": f"{type(e).__name__}: {e}"}
        return call

    return {name: _reported(fn) for name, fn in
            {"list_files": list_files, "read_file": read_file,
             "write_file": write_file, "edit_file": edit_file,
             "generate_media": generate_media,
             "compose_world": compose_world,
             "check_syntax": check_syntax,
             "play": play,
             "check_off": check_off,
             "done": done}.items()}
