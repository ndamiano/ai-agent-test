"""The bounded write/read/edit tools a codegen Fix dispatches through Services.

A game is a folder of ES modules. A fix prefers `edit_game_file` — a grounded anchor edit (exact
old→new, unique-or-fail) that CANNOT gut a file to a stub — and falls back to `write_game_file`
(whole-file overwrite, the escape hatch) only when edits can't land. `read_game_file` pulls a file
back.

Grounding is stateful. Every file has a `version` (bumped on each write/edit). A read/edit result
carries the current bytes and stamps the version the model has now SEEN. `edit_game_file` refuses to
apply against bytes the model hasn't seen at the current version — but it does NOT error empty: it
returns the current content in the SAME result, so the model re-anchors and retries in one turn.
Every content-bearing result therefore carries the latest file body; MessageBuilder file-keys these
so only the newest body per file survives in context.
"""

import re

from maestro.codegen.gates import game_dir, read_manifest


def _safe(name: str) -> str:
    """A flat .ts filename inside the game folder — no paths, no traversal."""
    name = (name or "main.ts").strip().replace("\\", "/").split("/")[-1]
    if not name.endswith(".ts"):
        name = re.sub(r"\.js$", "", name) + ".ts"
    return re.sub(r"[^A-Za-z0-9_.-]", "_", name)


def _planned_names(state) -> set:
    """The filenames the planner committed to (manifest) — the write allow-list. Empty before a plan
    exists (early authoring), which disables the guard until there's a plan to enforce."""
    return {_safe(f["name"]) for f in (read_manifest(state.run_dir).get("files") or []) if f.get("name")}


def build_codegen_tools(state) -> dict:
    versions: dict = {}   # filename -> current on-disk version (bumped every write/edit)
    seen: dict = {}       # filename -> version the model was last shown (via read or edit result)

    def _read(name: str) -> str:
        p = game_dir(state.run_dir) / name
        return p.read_text(encoding="utf-8") if p.exists() else ""

    def _bump(name: str, code: str) -> int:
        d = game_dir(state.run_dir)
        d.mkdir(parents=True, exist_ok=True)
        (d / name).write_text(code, encoding="utf-8")
        versions[name] = versions.get(name, 0) + 1
        seen[name] = versions[name]
        return versions[name]

    def read_game_file(file: str = "main.ts", **_) -> dict:
        name = _safe(file)
        content = _read(name)
        seen[name] = versions.get(name, 0)
        return {"ok": True, "file": name, "version": versions.get(name, 0), "content": content}

    def edit_game_file(file: str = "main.ts", old_string: str = "", new_string: str = "", **_) -> dict:
        name = _safe(file)
        cur = versions.get(name, 0)
        content = _read(name)
        if seen.get(name) != cur:
            seen[name] = cur
            return {"ok": False, "file": name, "version": cur, "content": content,
                    "error": "you didn't read before trying to edit. The read is now included in "
                             "this response — re-anchor old_string against it and retry."}
        if not old_string:
            return {"ok": False, "file": name, "version": cur, "content": content,
                    "error": "old_string was empty — copy the exact current text to replace."}
        n = content.count(old_string)
        if n == 0:
            return {"ok": False, "file": name, "version": cur, "content": content,
                    "error": "old_string not found — re-anchor on the current content below."}
        if n > 1:
            return {"ok": False, "file": name, "version": cur, "content": content,
                    "error": f"old_string matched {n} places — add surrounding lines to make it unique."}
        new_body = content.replace(old_string, new_string)
        v = _bump(name, new_body)
        return {"ok": True, "file": name, "version": v, "content": new_body}

    def write_game_file(code: str = "", file: str = "main.ts", **_) -> dict:
        if not code or not code.strip():
            return {"ok": False, "error": "empty code — output the complete file as one ```ts block"}
        name = _safe(file)
        planned = _planned_names(state)
        if planned and name not in planned:
            return {"ok": False, "error": f"off-plan filename {name!r} — write only a planned file: "
                                          f"{', '.join(sorted(planned))}"}
        v = _bump(name, code)
        return {"ok": True, "file": name, "version": v, "chars": len(code)}

    return {"write_game_file": write_game_file, "read_game_file": read_game_file,
            "edit_game_file": edit_game_file}
