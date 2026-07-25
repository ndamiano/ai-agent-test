"""The bounded write/read/edit tools a codegen Fix dispatches through Services.

A game is a folder of ES modules. `write` is CREATE-ONLY (authoring a file that doesn't exist yet);
every change to an existing file goes through `edit` — grounded anchor hunks (exact old→new,
unique-or-fail), applied ATOMICALLY as a batch. Overwrites after creation are banned: a whole-file
rewrite destabilizes correct code — reintroducing cleared bugs, re-rolling exported signatures
siblings depend on, re-inventing forbidden patterns. Edits are grounded and local; they cannot. `read_file`
pulls a file back — whole by default, or a line window (offset/limit) for a big file you only need a
slice of.

A read is bounded IN THE TOOL, not downstream: any physical line longer than MAX_LINE_CHARS is
collapsed to its head + an elision marker (a worldgen pre-seed bakes a 100KB heightfield onto ONE
`export const WORLD` line — the model needs the sibling FUNCTIONS, never the data payload, and a
line-window can't slice inside a single line), and the whole result is then capped to MAX_READ_CHARS
(head+tail, middle elided). Without this a 104KB file blows the message budget, gets dropped whole,
and the model re-reads it forever without ever writing. Elision/cap change the returned bytes but not
grounding: `edit` still validates old_string against the real on-disk content (unique-or-fail), so an
anchor on the elided region simply misses and re-anchors.

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

MAX_READ_CHARS = 16_000   # whole-read ceiling; a fat file returns head+tail, middle elided
MAX_LINE_CHARS = 2_000    # a single physical line past this is baked data, not code — collapse it


def _elide_long_lines(content: str, limit: int = MAX_LINE_CHARS) -> tuple:
    """Collapse any physical line longer than `limit` to head + a char-count marker. A line-window
    read can't slice inside one line, so a 100KB inline data literal has to be cut here or not at all."""
    lines = content.split("\n")
    elided = False
    for i, ln in enumerate(lines):
        if len(ln) > limit:
            elided = True
            lines[i] = ln[:200] + f" /* … {len(ln) - 200} chars of inline data elided … */"
    return "\n".join(lines), elided


def _cap_chars(content: str, limit: int = MAX_READ_CHARS) -> tuple:
    """Backstop: keep the head and tail of an over-long result, elide the middle."""
    if len(content) <= limit:
        return content, False
    half = limit // 2
    return (content[:half]
            + f"\n/* … {len(content) - limit} chars truncated — narrow with offset/limit … */\n"
            + content[-half:]), True


def _safe(name: str) -> str:
    """A flat .ts filename inside the game folder — no paths, no traversal."""
    name = (name or "main.ts").strip().replace("\\", "/").split("/")[-1]
    if not name.endswith(".ts"):
        name = re.sub(r"\.js$", "", name) + ".ts"
    return re.sub(r"[^A-Za-z0-9_.-]", "_", name)


def _fuzzy_spans(content: str, old: str) -> list:
    """Whitespace-tolerant hunk anchor: the (start, end) spans of every line window whose lines
    match old_string's lines after per-line strip. The model reproduces the code it means to
    replace with drifted indentation far more often than it picks the wrong code. Matching is by line
    CONTENT; the span (and so what survives around the replacement) is the file's real text.
    All-blank old_strings don't anchor."""
    old_lines = [ln.strip() for ln in old.splitlines()]
    if not old_lines or not any(old_lines):
        return []
    raw = content.splitlines(keepends=True)
    offsets, pos = [], 0
    for ln in raw:
        offsets.append(pos)
        pos += len(ln)
    spans = []
    for i in range(len(raw) - len(old_lines) + 1):
        if all(raw[i + j].strip() == old_lines[j] for j in range(len(old_lines))):
            start = offsets[i]
            last = raw[i + len(old_lines) - 1]
            end = offsets[i + len(old_lines) - 1] + len(last)
            if not old.endswith("\n"):
                end -= len(last) - len(last.rstrip("\r\n"))
            spans.append((start, end))
    return spans


def _planned_names(state) -> set:
    """The filenames the planner committed to (manifest) — the write allow-list. Empty before a plan
    exists (early authoring), which disables the guard until there's a plan to enforce."""
    return {_safe(f["name"]) for f in (read_manifest(state.run_dir).get("files") or []) if f.get("name")}


def build_codegen_tools(state, versions: dict = None, seen: dict = None) -> dict:
    # versions: filename -> current on-disk version (bumped every write/edit)
    # seen:     filename -> version the model was last shown (via read or edit result)
    # Passed in (by reference) when the fix subloop spans process deaths — the build cursor owns
    # them so a resumed `edit` still knows the file was read. Fresh dicts otherwise.
    versions = versions if versions is not None else {}
    seen = seen if seen is not None else {}

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
        raw = _read(name)
        partial = offset > 0 or limit is not None
        if not partial:
            seen[name] = versions.get(name, 0)
            content, elided = _elide_long_lines(raw)
            content, truncated = _cap_chars(content)
            return {"ok": True, "file": name, "version": versions.get(name, 0),
                    "elided": elided, "truncated": truncated, "content": content}
        lines = raw.splitlines(keepends=True)
        start = max(offset, 0)
        end = len(lines) if limit is None else min(start + max(limit, 0), len(lines))
        content, elided = _elide_long_lines("".join(lines[start:end]))
        content, truncated = _cap_chars(content)
        return {"ok": True, "file": name, "version": versions.get(name, 0), "partial": True,
                "offset": start, "shown_lines": end - start, "total_lines": len(lines),
                "elided": elided, "truncated": truncated, "content": content}

    def edit(file: str = "main.ts", edits: list = None, **_) -> dict:
        """Atomic multi-hunk edit: every hunk validates against the CURRENT content (non-empty,
        found, unique — uniqueness judged on the original for all hunks, and no two hunks may
        overlap the same span), then all apply and the version bumps ONCE. Any failure applies
        NOTHING and returns the current body, naming the offending hunk."""
        name = _safe(file)
        cur = versions.get(name, 0)
        content = _read(name)

        def fail(error: str) -> dict:
            return {"ok": False, "file": name, "version": cur, "content": content, "error": error}

        if content.lstrip().startswith("// GENERATED"):
            # Pipeline-owned file (control-scaffold main.ts, data.ts, worldgen's world.ts). Its
            # header line names where the change belongs; no content back — there is nothing to
            # re-anchor on.
            hint = content.lstrip().splitlines()[0].lstrip("/ ").strip()
            return {"ok": False, "file": name,
                    "error": f"{name} is a GENERATED file — never edit it. {hint} Make the change "
                             "in the file that owns the behavior instead."}
        if seen.get(name) != cur:
            seen[name] = cur
            return fail("you didn't read before trying to edit. The read is now included in "
                        "this response — re-anchor your hunks against it and retry.")
        hunks = edits or []
        if not hunks:
            return fail('no edits — pass edits=[{"old_string": ..., "new_string": ...}, ...].')
        spans = []
        appends = []
        fuzzy = 0
        for i, h in enumerate(hunks, 1):
            old = (h or {}).get("old_string", "")
            if not old:
                # Empty old_string = APPEND at end of file: a one-line append via anchor-replace
                # would need the file's tail as its anchor, the flimsiest anchor there is. Still
                # read-grounded (the seen-version gate above) and atomic with the batch.
                if not (h or {}).get("new_string", "").strip():
                    return fail(f"hunk {i}/{len(hunks)}: both strings empty — an append hunk needs "
                                "new_string.")
                appends.append(h)
                continue
            n = content.count(old)
            if n > 1:
                return fail(f"hunk {i}/{len(hunks)}: old_string matched {n} places — add "
                            "surrounding lines to make it unique. Nothing was applied.")
            if n == 1:
                start = content.index(old)
                end = start + len(old)
            else:
                near = _fuzzy_spans(content, old)
                if not near:
                    return fail(f"hunk {i}/{len(hunks)}: old_string not found — re-anchor on the "
                                "current content below. Nothing was applied.")
                if len(near) > 1:
                    return fail(f"hunk {i}/{len(hunks)}: old_string matched {len(near)} places "
                                "(ignoring indentation) — add surrounding lines to make it "
                                "unique. Nothing was applied.")
                start, end = near[0]
                fuzzy += 1
            for j, (s2, e2) in enumerate(spans, 1):
                if start < e2 and s2 < end:
                    return fail(f"hunk {i}/{len(hunks)} overlaps hunk {j}'s text — merge them into "
                                "one hunk. Nothing was applied.")
            spans.append((start, end))
        # Splice by the spans validated on the ORIGINAL content (right-to-left so offsets hold) —
        # sequential replace() could land inside an earlier hunk's replacement text.
        new_body = content
        replace_hunks = [h for h in hunks if (h or {}).get("old_string", "")]
        for (start, end), h in sorted(zip(spans, replace_hunks), key=lambda p: p[0], reverse=True):
            new_body = new_body[:start] + h.get("new_string", "") + new_body[end:]
        for h in appends:
            sep = "" if (not new_body or new_body.endswith("\n")) else "\n"
            new_body = new_body + sep + h["new_string"]
        if content.strip() and not new_body.strip():
            # A hunk spanning the whole body with an empty replacement is found, unique and
            # non-overlapping — every check above passes and the file is gone.
            return fail("that edit would leave the file empty — it deletes the whole body instead "
                        "of fixing it. Edit the lines that are wrong and leave the rest.")
        v = _bump(name, new_body)
        out = {"ok": True, "file": name, "version": v, "applied": len(hunks), "content": new_body}
        if fuzzy:
            out["fuzzy"] = fuzzy   # hunks that landed via whitespace-tolerant anchoring (telemetry)
        return out

    def write(code: str = "", file: str = "main.ts", **_) -> dict:
        if not code or not code.strip():
            return {"ok": False, "error": "empty code — output the complete file as one ```ts block"}
        name = _safe(file)
        planned = _planned_names(state)
        if planned and name not in planned:
            return {"ok": False, "error":
                    f"off-plan filename {name!r} — you can ONLY write: {', '.join(sorted(planned))}. "
                    f"Never import from {name!r}: define its contents (types, helpers) inside a "
                    "planned file instead."}
        existing = _read(name)
        if existing.strip():
            # Same re-anchor convention as an edit miss: hand back the body + stamp seen, so the
            # refused turn converts straight into a grounded edit.
            seen[name] = versions.get(name, 0)
            return {"ok": False, "file": name, "version": versions.get(name, 0), "content": existing,
                    "error": f"{name} already exists — overwrites are not allowed; use edit "
                             "(you can pass several hunks in one call)"}
        v = _bump(name, code)
        return {"ok": True, "file": name, "version": v, "chars": len(code)}

    return {"write": write, "read_file": read_file,
            "edit": edit}
