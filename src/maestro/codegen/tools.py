"""The tools the build dispatches: list_files, read_file, write_file, edit_file, generate_media, done.

Two invariants. A path is resolved and must land inside the game folder, so no write can escape it.
And every failure is REPORTED to the model rather than guessed at — see `_reported`.
"""

import json
import os
from pathlib import Path

from maestro.codegen.assets import request_media
from maestro.codegen.staging import game_dir

MAX_READ_CHARS = 20_000   # whole-file ceiling; past this the read returns the head and says so.
                          # Higher than build_steps._MAX_TOOL_CHARS is a promise the transcript cuts.


_ESCAPES = {"\\r\\n": "\n", "\\n": "\n", "\\t": "\t", "\\\"": "\"", "\\'": "'"}


def _unescaped(text: str) -> str:
    for k, v in _ESCAPES.items():
        text = text.replace(k, v)
    return text


DOUBLE_ESCAPED = ("Your text is escaped twice — it carries backslash sequences (\\n, \\\") where "
                  "the file has a real newline or quote. Send the characters themselves, escaped "
                  "once for JSON.")


def _safe(root: Path, path: str) -> Path:
    """A path inside the game folder. Resolved, so `../` can never escape. A missing path raises
    KeyError('path') so the reported error names the argument that was left out."""
    if not path:
        raise KeyError("path")
    p = (root / path).resolve()
    if not str(p).startswith(str(root.resolve()) + os.sep):
        raise ValueError(f"path escapes the project directory: {path!r}")
    return p


def build_tools(state) -> dict:
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

    def write_file(path=None, content=None, **_) -> dict:
        p = _safe(root, path)
        if content is None:
            raise KeyError("content")
        if "\n" not in content and "\\n" in content:
            return {"ok": False, "error": DOUBLE_ESCAPED}
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
        return {"ok": True, "path": path, "chars": len(content)}

    def edit_file(path: str = None, old_text=None, new_text=None, **_) -> dict:
        p = _safe(root, path)
        if not p.exists():
            return {"ok": False, "error": f"no such file: {path}"}
        if old_text is None:
            raise KeyError("old_text")
        if new_text is None:
            # An omitted new_text would silently DELETE the matched region. Deleting is legitimate,
            # but only when the model sends "" and means it.
            raise KeyError("new_text")
        old, new = old_text, new_text
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

    def generate_media(id=None, prompt=None, kind="image", **_) -> dict:
        # `kind` is the one argument with a default, because the schema offers it as optional.
        return request_media(state.run_id, state.run_dir, id, prompt, kind or "image")

    def _reported(fn):
        """A tool result is a BOUNDARY: anything the call raises comes back as text the model can
        act on. Never substitute a default for a bad argument — the report is what lets it retry."""
        def call(**kw):
            try:
                return fn(**kw)
            except Exception as e:
                return {"ok": False, "error": f"{type(e).__name__}: {e}"}
        return call

    return {name: _reported(fn) for name, fn in
            {"list_files": list_files, "read_file": read_file,
             "write_file": write_file, "edit_file": edit_file,
             "generate_media": generate_media}.items()}
