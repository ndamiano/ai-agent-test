"""The tools the build dispatches: list_files, read_file, write_file, edit_file, generate_media,
compose_world.

Two invariants. A path is resolved and must land inside the game folder, so no write can escape it.
And every failure is REPORTED to the model rather than guessed at — see `_reported`.
"""

import os
from pathlib import Path

from maestro.codegen.assets import DEFAULT_KIND, read_manifest, request_media
from maestro.codegen.staging import RUNTIME_DIR, game_dir

_VENDOR_FILES = {p.name for p in (RUNTIME_DIR / "vendor").glob("*.js")}

# A read lands in a PROGRAM'S variable, not in the transcript — only what the program prints costs
# the window — so there is no reason to cut one short. A ceiling here was a context guard, and
# under a program it guards nothing while making a partial read look like a whole one: the model
# discards the truncation note it never printed and then reasons from a fragment (measured
# 2026-09-07: counted occurrences over 43% of a file and read the zeroes as missing edits).
MAX_READ_CHARS = 8_000_000


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

    def read_file(path: str = None, offset=None, lines=None, **_) -> dict:
        """A window of the file, whole lines, starting at 1-based `offset`, at most `lines` long.

        The window ends on a line boundary: a cut mid-line is text the model copies into old_text,
        where it matches nothing (measured 2026-07-29: 12 byte-identical failing edits, 248 of 249
        chars matching, the 249th the cut). `offset` is what makes the tail past the ceiling
        reachable at all."""
        p = _safe(root, path)
        if not p.exists():
            # A requested-but-unrendered asset is not a missing file: "no such file" reads as a
            # failed ask and the model re-requests its art under new ids. ok=True keeps the
            # repeat ledger from scolding a legitimate second look.
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
            # A single line past the ceiling: cutting it is the only bound left, so say where.
            content = content[:MAX_READ_CHARS] + (
                f"\n\n[line {end} is longer than one read and was cut here, mid-line — text ending "
                f"at that cut is not what the file says, so do not use it as an edit anchor.]")
        elif end < total and not (want and end - start >= want):
            content += (
                f"\n\n[showed lines {start + 1}-{end} of {total}; read the rest with offset "
                f"{end + 1}.]")
        return {"ok": True, "path": path, "content": content, "lines": f"{start + 1}-{end}/{total}"}

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

    def generate_media(id=None, kind=DEFAULT_KIND, subject=None, style=None, details=None,
                       **_) -> dict:
        return request_media(state.run_id, state.run_dir, build_id, id, subject, style,
                             kind or DEFAULT_KIND, details)

    def compose_world(description=None, seed=None, **_) -> dict:
        # One world per game: a second one would replace the ground under a game already
        # written against the first one's metres and regions.
        from maestro.worldgen.compose import compose, world_dir
        if not description:
            raise KeyError("description")
        if (world_dir(root) / "world.json").exists():
            return {"ok": False,
                    "error": "this game already has its world — read world/world.json for its "
                             "size and regions instead of building another."}
        return compose(root, state.run_dir, state.run_id, build_id, description,
                       int(seed) if seed is not None else None)

    def check_syntax(paths=None, **_) -> dict:
        """Does the game's JavaScript parse? The error gate's own parser, run HERE — the program
        that asks is confined and cannot start anything itself.

        It only ever answers BROKEN or not: a file parses or it does not, and a parse error can
        only be satisfied by fixing the syntax. Nothing here judges what the code DOES."""
        from maestro.codegen.error_gate import _node_message
        if paths is None:
            paths = [str(p.relative_to(root)) for p in sorted(root.rglob("*.js"))
                     if p.name not in _VENDOR_FILES and p.parent != root / "lib"]
        elif isinstance(paths, str):
            paths = [paths]
        out = {}
        for rel in paths:
            p = _safe(root, rel)
            if not p.exists():
                out[rel] = f"no such file: {rel}"
                continue
            out[rel] = _node_message(p.read_text(encoding="utf-8", errors="replace")) or "OK"
        return {"ok": True, "checked": out}

    def done(summary=None, **_) -> dict:
        """The build says it is finished. Nothing happens here: the driver reads `done` off the
        ledger of what the program called, answers the FIRST one with the nudge, and ends the
        build on the second."""
        return {"ok": True, "summary": str(summary or "")}

    def _reported(fn):
        """A tool result is a BOUNDARY: anything the call raises comes back as text the model can
        act on. Never substitute a default for a bad argument — the report is what lets it retry."""
        def call(**kw):
            try:
                return fn(**kw)
            except KeyError as e:
                sent = ", ".join(sorted(kw)) or "no arguments"
                return {"ok": False, "error": f"{fn.__name__} needs the argument {e.args[0]!r}; "
                                              f"this call sent {sent}."}
            except Exception as e:
                return {"ok": False, "error": f"{type(e).__name__}: {e}"}
        return call

    return {name: _reported(fn) for name, fn in
            {"list_files": list_files, "read_file": read_file,
             "write_file": write_file, "edit_file": edit_file,
             "generate_media": generate_media,
             "compose_world": compose_world,
             "check_syntax": check_syntax,
             "done": done}.items()}
