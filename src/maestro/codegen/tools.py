"""The bounded write/read/edit tools a codegen Fix dispatches through Services.

A game is a folder of ES modules. A fix prefers `edit` — a grounded anchor edit (exact
old→new, unique-or-fail) that CANNOT gut a file to a stub — and falls back to `write`
(whole-file overwrite, the escape hatch) only when edits can't land. `read_file` pulls a file back —
whole by default, or a line window (offset/limit) for a big file you only need a slice of.

Grounding is stateful. Every file has a `version` (bumped on each write/edit). A read/edit result
carries the current bytes and stamps the version the model has now SEEN. `edit` refuses to
apply against bytes the model hasn't seen at the current version — but it does NOT error empty: it
returns the current content in the SAME result, so the model re-anchors and retries in one turn.
A FULL `read_file` (no offset/limit) stamps seen; a PARTIAL read does NOT — you cannot anchor an edit
against a slice you've only partly seen, so a window read still forces a full read before editing.
Every content-bearing result therefore carries the latest file body; MessageBuilder file-keys FULL
reads/edits so only the newest body per file survives — a partial read keys normally so a slice never
evicts the full body.
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

    def read_file(file: str = "main.ts", offset: int = 0, limit: int = None, **_) -> dict:
        name = _safe(file)
        content = _read(name)
        partial = offset > 0 or limit is not None
        if not partial:
            seen[name] = versions.get(name, 0)
            return {"ok": True, "file": name, "version": versions.get(name, 0), "content": content}
        lines = content.splitlines(keepends=True)
        start = max(offset, 0)
        end = len(lines) if limit is None else min(start + max(limit, 0), len(lines))
        return {"ok": True, "file": name, "version": versions.get(name, 0), "partial": True,
                "offset": start, "shown_lines": end - start, "total_lines": len(lines),
                "content": "".join(lines[start:end])}

    def edit(file: str = "main.ts", old_string: str = "", new_string: str = "", **_) -> dict:
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

    def write(code: str = "", file: str = "main.ts", **_) -> dict:
        if not code or not code.strip():
            return {"ok": False, "error": "empty code — output the complete file as one ```ts block"}
        name = _safe(file)
        planned = _planned_names(state)
        if planned and name not in planned:
            return {"ok": False, "error": f"off-plan filename {name!r} — write only a planned file: "
                                          f"{', '.join(sorted(planned))}"}
        v = _bump(name, code)
        return {"ok": True, "file": name, "version": v, "chars": len(code)}

    return {"write": write, "read_file": read_file,
            "edit": edit}
