"""The five tools the build dispatches: list_files, read_file, write_file, edit_file, done.

These are deliberately the SMALLEST tools that work, and they are kept that way. A 25-game grid
measured this exact surface producing playable games; every refinement layered on afterwards — read
grounding, atomic multi-hunk edits, whitespace-fuzzy anchoring, mid-file elision — was added for the
old kit pipeline and, carried over here, coincided with a regression. Each is re-addable, but only
one at a time and only when a measurement says it earned its place.

The one thing that is NOT negotiable is the path guard: a path is resolved and must land inside the
game folder, so no write can escape it.
"""

import json
import os
from pathlib import Path

from maestro.codegen.staging import game_dir

MAX_READ_CHARS = 60_000   # whole-file ceiling; past this the read returns the head and says so


class MissingArg(Exception):
    """A required tool argument the model didn't send."""


def _require(name: str, value):
    """A missing required argument is an ERROR the model gets told about, never a default.

    Measured: `write_file` quietly defaulting a missing `path` to "index.html" meant every write in
    a run landed on the same file and the last one — the game's JavaScript — won, so index.html held
    no HTML and the page rendered its own source. The model omits `path` on roughly a quarter of
    calls; told so, it immediately resends the call correctly."""
    if value is None or (isinstance(value, str) and not value.strip()):
        raise MissingArg(name)
    return value


def _safe(root: Path, path: str) -> Path:
    """A path inside the game folder. Resolved, so `../` can never escape."""
    p = (root / _require("path", path)).resolve()
    if not str(p).startswith(str(root.resolve()) + os.sep):
        raise ValueError(f"path escapes the project directory: {path!r}")
    return p


def _as_text(value) -> tuple:
    """(text, error) for a string argument the model sent. A wrong TYPE comes back as a tool error
    it can act on — raising kills the completion that would have told it. A dict/list is
    JSON-serialized: a model writing a .json file sends the object, and that is what it meant."""
    if isinstance(value, str):
        return value, ""
    if value is None:
        return "", ""
    if isinstance(value, (dict, list)):
        return json.dumps(value, indent=2, ensure_ascii=False), ""
    if isinstance(value, (int, float, bool)):
        return str(value), ""
    return "", f"expected text, got {type(value).__name__}"


def build_tools(state, versions: dict = None, seen: dict = None) -> dict:
    # versions/seen are unused now that reads don't gate edits; the build cursor still carries them
    # so an in-flight build survives this change, and they cost nothing.
    root = game_dir(state.run_dir)

    def list_files(**_) -> dict:
        if not root.exists():
            return {"ok": True, "files": []}
        out = []
        for p in sorted(root.rglob("*")):
            if p.is_file() and not p.name.startswith("_"):
                out.append({"path": str(p.relative_to(root)), "bytes": p.stat().st_size})
        return {"ok": True, "files": out}

    def read_file(path: str = None, **_) -> dict:
        p = _safe(root, path)
        if not p.exists():
            return {"ok": False, "error": f"no such file: {path}"}
        body = p.read_text(encoding="utf-8", errors="replace")
        if len(body) > MAX_READ_CHARS:
            return {"ok": True, "path": path, "content": body[:MAX_READ_CHARS]
                    + f"\n\n[truncated: file is {len(body)} chars, showed the first {MAX_READ_CHARS}]"}
        return {"ok": True, "path": path, "content": body}

    def write_file(path: str = None, content=None, **_) -> dict:
        text, err = _as_text(_require("content", content))
        if err:
            return {"ok": False, "error": f"`content` {err}. Send the complete file as a string."}
        p = _safe(root, path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
        return {"ok": True, "path": path, "chars": len(text)}

    def edit_file(path: str = None, old_text=None, new_text=None, **_) -> dict:
        p = _safe(root, path)
        if not p.exists():
            return {"ok": False, "error": f"no such file: {path}"}
        old, err = _as_text(_require("old_text", old_text))
        if err:
            return {"ok": False, "error": f"`old_text` {err}."}
        new, err = _as_text(new_text if new_text is not None else "")
        if err:
            return {"ok": False, "error": f"`new_text` {err}."}
        body = p.read_text(encoding="utf-8")
        n = body.count(old) if old else 0
        if not old:
            return {"ok": False, "error": "old_text is empty — send the exact text to replace."}
        if n == 0:
            return {"ok": False, "error": "old_text was not found in the file. Read the file and "
                                          "copy the exact text, including whitespace."}
        if n > 1:
            return {"ok": False, "error": f"old_text appears {n} times. Include more surrounding "
                                          "text to make it unique."}
        p.write_text(body.replace(old, new), encoding="utf-8")
        return {"ok": True, "path": path, "chars": len(new)}

    def _reported(fn):
        def call(**kw):
            try:
                return fn(**kw)
            except MissingArg as e:
                return {"ok": False, "error": f"missing required argument: {e}. "
                                              "Send the call again with every argument."}
            except ValueError as e:
                return {"ok": False, "error": str(e)}
        return call

    return {name: _reported(fn) for name, fn in
            {"list_files": list_files, "read_file": read_file,
             "write_file": write_file, "edit_file": edit_file}.items()}
