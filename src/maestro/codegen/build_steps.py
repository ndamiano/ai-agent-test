"""The fix-shape step machines — the old synchronous fix bodies, re-expressed as resumable steps.

Each shape is a function `step(spec, run_dir, tools, fc, result) -> Infer | Done`. When the fix has
no LLM result to apply yet (`fc.started` is False) it builds and returns the FIRST inference request;
otherwise it applies the completed turn's `result` and either returns the NEXT request (the fix isn't
done) or `Done` (back to the outer gate sweep). The driver (build_chain) enqueues each `Infer` as one
`llm` job, dies, and re-enters this function on the completion — so a shape's whole loop is spread
across process deaths, its scratch carried in `fc` (build_state.json).

This is a faithful port of module.py's `_plan_fix` / `_data_fix` / `_author_via_write` /
`_read_write_loop_fix`: same prompts, same parsing, same break-out conditions — only the control flow
is inverted from "call infer and use the return" to "return the request, resume with the result." All
the prompt-building + parsing helpers are imported from module.py unchanged.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import List, Optional, Union

from llm_clients.message_builder import MessageBuilder
from maestro.codegen import data_files
from maestro.codegen.fix_classes import (
    classify,
    strip_dead_creategame,
    strip_unplanned_imports,
)
from maestro.codegen.gates import extract_code, game_files, manifest_path
from maestro.codegen.module import (
    _CODE_MAX_TOKENS,
    _DATA_MAX_TOKENS,
    _PLAN_MAX_TOKENS,
    _PROMPTS,
    _WRITE_SCHEMA,
    _FIX_LOOP_MAX_TURNS,
    _contract_block,
    _design_block,
    _fix_schemas,
    _hook_exports,
    _is_contract,
    _is_stub,
    _json_from,
    _kit_context,
    _manifest_files,
    _seeded_block,
    _sibling_lines,
)
from maestro.codegen.scaffold import ENTRY_HOOK
from maestro.services import parse_args, salvage_tool_call


# ── step outcomes ─────────────────────────────────────────────────────────────
@dataclass
class Infer:
    """Suspend the fix here: enqueue one `llm` job with these messages, die, resume on completion."""
    messages: List[dict]
    schemas: List[dict]
    max_tokens: int
    reasoning: Optional[str] = None
    report: Optional[str] = None      # progress line emitted when this turn is enqueued


@dataclass
class Done:
    """The fix is finished — return to the outer gate sweep."""
    report: str


Outcome = Union[Infer, Done]


def _content(result) -> str:
    return ((result.get("choices") or [{}])[0].get("message", {}) or {}).get("content", "") or ""


# ── plan ──────────────────────────────────────────────────────────────────────
def plan_step(spec, run_dir, tools, fc, result) -> Outcome:
    if not fc.started:
        fc.started = True
        system = (_PROMPTS / "plan_game.txt").read_text(encoding="utf-8")
        user = (f"{_design_block(spec)}{_seeded_block(run_dir)}\n\n"
                "Plan the files. Output ONLY one ```json block.")
        msgs = MessageBuilder(system).add_user(user).build()
        return Infer(msgs, [], _PLAN_MAX_TOKENS, report="planning the file list")
    text = _content(result)
    m = re.search(r"```(?:json)?\s*\n(.*?)```", text, re.S)
    generated = {n for n, src in game_files(run_dir).items()
                 if src.lstrip().startswith("// GENERATED")}
    try:
        manifest = json.loads(m.group(1) if m else text)
        files = [f for f in (manifest.get("files") or []) if f.get("name")]
        files = [f for f in files if f["name"] not in generated]
        assert any(f["name"] == ENTRY_HOOK for f in files)
        manifest = {"files": files}
    except Exception:
        manifest = {"files": [{"name": ENTRY_HOOK,
                               "purpose": "the whole game behind the scaffold hooks",
                               "exports": _hook_exports(spec)}]}
    manifest_path(run_dir).parent.mkdir(parents=True, exist_ok=True)
    manifest_path(run_dir).write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    names = ", ".join(f["name"] for f in manifest["files"])
    return Done(f"planned {len(manifest['files'])} file(s): {names}")


# ── data (design | fix rows) ──────────────────────────────────────────────────
def data_step(spec, run_dir, tools, fc, result) -> Outcome:
    is_design = fc.error.get("type") == "build"
    if not fc.started:
        fc.started = True
        if is_design:
            return _data_design_request(spec, run_dir)
        return _data_fix_request(run_dir) or Done(
            "data invalid but no dataset attributable — regenerate the design")
    return _data_design_apply(run_dir, result) if is_design else _data_fix_apply(run_dir, result)


def _data_design_request(spec, run_dir) -> Infer:
    system = (_PROMPTS / "design_data.txt").read_text(encoding="utf-8")
    filelist = "\n".join(f"- {f['name']}: {f.get('purpose', '')}"
                         for f in _manifest_files(run_dir)) or "(none planned yet)"
    user = (f"{_design_block(spec)}\n\n# PLANNED FILES\n{filelist}\n\n"
            "Design the data files. Output ONLY one ```json block.")
    return Infer(MessageBuilder(system).add_user(user).build(), [], _DATA_MAX_TOKENS,
                 report="designing the data files")


def _data_design_apply(run_dir, result) -> Done:
    try:
        design = _json_from(_content(result))
        datasets = design.get("datasets")
        assert isinstance(datasets, list)
    except Exception:
        datasets = []
    dropped = data_files.write_design(run_dir, datasets)
    data_files.generate_data_ts(run_dir)
    kept = [d["name"] for d in data_files.read_data_manifest(run_dir).get("datasets", [])]
    msg = f"designed {len(kept)} dataset(s): {', '.join(kept) or '(none)'}"
    if dropped:
        msg += f" — dropped invalid: {', '.join(dropped)}"
    return Done(msg)


def _data_fix_request(run_dir) -> Optional[Infer]:
    manifest = data_files.read_data_manifest(run_dir)
    violations = data_files.validate_data(run_dir)
    declared = {d.get("name") for d in (manifest.get("datasets") or []) if isinstance(d, dict)}
    offending = data_files.offending_datasets(violations, declared)
    if not offending:
        return None
    target = offending[0]
    rows_path = data_files.data_dir(run_dir) / f"{target}.json"
    raw = rows_path.read_text(encoding="utf-8") if rows_path.exists() else "[]"
    system = (_PROMPTS / "fix_data.txt").read_text(encoding="utf-8")
    user = "\n\n".join([
        f"# DATA MANIFEST (declared fields — the types are law)\n```json\n"
        f"{json.dumps(manifest, indent=1, ensure_ascii=False)}\n```",
        f"# CURRENT ROWS: data/{target}.json\n```json\n{raw}\n```",
        "# VIOLATIONS\n" + "\n".join(f"- {v}" for v in violations),
        f"Fix dataset '{target}'. Output ONLY one ```json block: the corrected rows array.",
    ])
    return Infer(MessageBuilder(system).add_user(user).build(), [], _DATA_MAX_TOKENS,
                 report=f"fixing data/{target}.json")


def _data_fix_apply(run_dir, result) -> Done:
    violations = data_files.validate_data(run_dir)
    manifest = data_files.read_data_manifest(run_dir)
    declared = {d.get("name") for d in (manifest.get("datasets") or []) if isinstance(d, dict)}
    offending = data_files.offending_datasets(violations, declared)
    if not offending:
        return Done("data invalid but no dataset attributable — regenerate the design")
    target = offending[0]
    rows_path = data_files.data_dir(run_dir) / f"{target}.json"
    try:
        rows = _json_from(_content(result))
        assert isinstance(rows, list)
    except Exception:
        return Done(f"data fix for '{target}' returned no rows array")
    rows_path.write_text(json.dumps(rows, indent=2, ensure_ascii=False), encoding="utf-8")
    remaining = data_files.validate_data(run_dir)
    if not remaining:
        data_files.generate_data_ts(run_dir)
    return Done(f"rewrote data/{target}.json ({len(rows)} rows), {len(remaining)} violation(s) remain")


# ── author (one file, ≤2 turns) ───────────────────────────────────────────────
def author_step(spec, run_dir, tools, fc, result) -> Outcome:
    name = fc.error.get("path")
    if not fc.started:
        fc.started = True
        return _author_request(spec, run_dir, name, report=f"authoring {name}")
    code = _extract_write_code(result)
    if not code.strip() and fc.attempt < 1:
        # The model thinks in-content and a long think can truncate the tool call mid-arg → no code.
        # One retry usually lands, exactly as _author_via_write's range(2).
        fc.attempt = 1
        return _author_request(spec, run_dir, name, report=f"authoring {name} (retry)")
    res = tools["write"](code=code, file=name)
    detail = res.get("error") or f"{res.get('chars')} chars"
    if not res.get("error"):
        planned = {f["name"] for f in _manifest_files(run_dir)}
        if strip_unplanned_imports(run_dir, planned):
            detail += " (stripped unplanned import)"
        if strip_dead_creategame(run_dir):
            detail += " (stripped dead createGame)"
    return Done(f"authored {name}: {detail}")


def _author_request(spec, run_dir, name, report) -> Infer:
    files = _manifest_files(run_dir)
    me = next((f for f in files if f["name"] == name),
              {"name": name, "purpose": "", "exports": []})
    system = (_PROMPTS / "author_file.txt").read_text(encoding="utf-8")
    parts = [
        f"# KIT API\n{_kit_doc_for(spec)}",
        _design_block(spec),
        f"# THIS FILE: {me['name']}\npurpose: {me.get('purpose', '')}\n"
        f"must export: {', '.join(me.get('exports') or []) or '(none)'}",
    ]
    contract = _contract_block(run_dir, me["name"])
    if contract:
        parts.append("# SHARED TYPES — author against these EXACT shapes; do NOT invent fields that "
                     f"aren't here (import the types you need from their file)\n{contract}")
    data = data_files.data_summary(run_dir)
    if data:
        parts.append(data)
    parts.append(f"# OTHER FILES you may import (signatures only)\n{_sibling_lines(run_dir, me['name'])}")
    if _is_contract(me):
        parts.append((_PROMPTS / "contract_rules.txt").read_text(encoding="utf-8"))
    parts.append(f"Call write to create ./{me['name']} now — the whole file as the `code` arg.")
    msgs = MessageBuilder(system).add_user("\n\n".join(parts)).build()
    return Infer(msgs, [_WRITE_SCHEMA], _CODE_MAX_TOKENS, report=report)


def _extract_write_code(result) -> str:
    message = (result.get("choices") or [{}])[0].get("message", {}) or {}
    content = message.get("content", "") or ""
    tcs = [tc for tc in (message.get("tool_calls") or [])
           if tc.get("function", {}).get("name") == "write"]
    if not tcs:
        salvaged = salvage_tool_call(content, [_WRITE_SCHEMA])
        tcs = [salvaged] if salvaged else []
    return parse_args(tcs[0]["function"].get("arguments")).get("code", "") if tcs else extract_code(content)


# ── read_write (the ≤8-turn read→edit subloop) ────────────────────────────────
def read_write_start(spec, run_dir, fc) -> Infer:
    """Build the first request: assemble the fix_loop prompt (system = fix_loop.txt + kit + contract;
    user = design + failing gate + the fix class's authority/directive). The system + the growing
    transcript live in `fc` and are re-sent each turn."""
    from maestro.codegen.build_state import error_from_dict
    error = error_from_dict(fc.error)
    cls = classify(error)
    files = _manifest_files(run_dir)
    filelist = "\n".join(
        f"- {f['name']}: {f.get('purpose', '')} (exports: {', '.join(f.get('exports') or []) or 'none'})"
        for f in files) or "\n".join(f"- {n}" for n in game_files(run_dir))
    system = (_PROMPTS / "fix_loop.txt").read_text(encoding="utf-8")
    kit = _kit_context(spec, error)
    if kit:
        system = f"{system}\n\n{kit}"
    authority = cls.authority(spec, run_dir, error) if cls.authority else ""
    directive = cls.directive or ""
    contract_inv = ((_PROMPTS / "contract_invariant.txt").read_text(encoding="utf-8")
                    if any(_is_contract(f) for f in files) else "")
    user = "\n\n".join(p for p in [
        _design_block(spec),
        contract_inv,
        f"# FILES (read any you need — you are NOT shown their bodies)\n{filelist}",
        data_files.data_summary(run_dir),
        f"# FAILING GATE\n{error.message}",
        authority,
        directive,
        "Read whatever files you need to find the root cause, then fix it with edit — the smallest "
        "hunks that fix the failure, every needed hunk (definition + call sites) in the same "
        "completion.",
    ] if p)
    fc.system = system
    fc.history = [{"role": "user", "content": user}]
    fc.started = True
    return _read_write_infer(fc)


def _read_write_infer(fc) -> Infer:
    schemas = _fix_schemas(fc.escalate, fc.nreads)
    msgs = MessageBuilder(fc.system).extend(fc.history).build()
    # reasoning OFF: the fix is a bounded read→act loop; thinking-on burns the token budget and
    # starves the tool call. The reads do the diagnosis empirically.
    return Infer(msgs, schemas, _CODE_MAX_TOKENS, reasoning="none",
                 report=f"fixing (turn {fc.turn + 1}/{_FIX_LOOP_MAX_TURNS})")


def read_write_apply(spec, run_dir, tools, fc, result) -> Outcome:
    message = (result.get("choices") or [{}])[0].get("message", {}) or {}
    content = message.get("content", "") or ""
    tcs = [tc for tc in (message.get("tool_calls") or []) if tc.get("function", {}).get("name")]
    if not tcs:
        salvaged = salvage_tool_call(content, _fix_schemas(fc.escalate, fc.nreads))
        tcs = [salvaged] if salvaged else []
    if len(content) > 2000:
        content = "[…analysis truncated…]\n" + content[-2000:]
    if not tcs:
        fc.history.append({"role": "assistant", "content": content})
        fc.history.append({"role": "user",
                           "content": "Call a tool: read_file to inspect a file, edit to apply "
                                      "hunks, or write to create a missing file."})
    else:
        fc.history.append({"role": "assistant", "content": content, "tool_calls": tcs})
        for tc in tcs:
            _apply_tool_call(tools, fc, tc)
        if fc.wrote:
            return Done(f"patched {fc.wrote} via {fc.mode} "
                        f"(read {fc.nreads}, edit-miss {fc.edit_fails})")
    fc.turn += 1
    if fc.turn >= _FIX_LOOP_MAX_TURNS:
        return Done(f"fix loop ended without a write (read {fc.nreads}, edit-miss {fc.edit_fails})")
    return _read_write_infer(fc)


def _apply_tool_call(tools, fc, tc) -> None:
    name = tc["function"]["name"]
    args = parse_args(tc["function"].get("arguments"))
    if name == "write":
        code = args.get("code", "")
        if _is_stub(code):
            res = {"ok": False, "error": "that is a stub/placeholder, not the complete file — "
                   "resend the ENTIRE working source in `code`."}
        else:
            res = tools["write"](code=code, file=args.get("file", "main.ts"))
            if res.get("ok"):
                fc.wrote, fc.mode = res.get("file"), "write"
        fc.history.append({"role": "tool", "tool_call_id": tc.get("id"), "content": json.dumps(res)})
    elif name == "edit":
        res = tools["edit"](file=args.get("file", "main.ts"), edits=args.get("edits") or [])
        if res.get("ok"):
            fc.wrote, fc.mode = res.get("file"), "edit"
        else:
            fc.edit_fails += 1
        fc.history.append({"role": "tool", "tool_call_id": tc.get("id"), "content": json.dumps(res)})
    elif name == "read_file":
        read_args = {"file": args.get("file", "main.ts")}
        if args.get("offset") is not None:
            read_args["offset"] = args["offset"]
        if args.get("limit") is not None:
            read_args["limit"] = args["limit"]
        res = tools["read_file"](**read_args)
        fc.nreads += 1
        fc.history.append({"role": "tool", "tool_call_id": tc.get("id"), "content": json.dumps(res)})
    else:
        fc.history.append({"role": "tool", "tool_call_id": tc.get("id"),
                           "content": f"unknown tool: {name!r}"})


# ── audit (the spec-vs-code sweep, one turn + one retry) ──────────────────────
def audit_step(spec, run_dir, tools, fc, result) -> Outcome:
    """One audit sweep: a verdict per spec claim, the failed ones harvested into `fc.findings` for
    the driver to queue as fixes. A reply still unparseable after the retry yields zero findings —
    an audit failure must never strand a green build."""
    from maestro.codegen import audit
    claims = audit.claims_of(spec)
    if not claims:
        return Done("audit: spec enumerates no claims — passing")
    if not fc.started:
        fc.started = True
        msgs, max_tokens = audit.build_request(spec, run_dir, claims)
        return Infer(msgs, [], max_tokens, reasoning="none",
                     report=f"auditing {len(claims)} spec claim(s)")
    entries, why = audit.parse_verdicts(_content(result), len(claims))
    if entries is None:
        if fc.attempt < 1:
            fc.attempt = 1
            msgs, max_tokens = audit.build_request(spec, run_dir, claims, retry=True)
            return Infer(msgs, [], max_tokens, reasoning="none",
                         report=f"auditing (retry: {why})")
        return Done(f"audit reply invalid after retry ({why}) — passing")
    findings, dropped = audit.findings_from(claims, entries)
    fc.findings = findings
    delivered = sum(1 for e in entries if str(e.get("status", "")).lower() == "delivered")
    blocked = sum(1 for e in entries if str(e.get("status", "")).lower() == "blocked")
    report = (f"audit: {delivered}/{len(claims)} delivered, {blocked} blocked, "
              f"{len(findings)} finding(s) queued")
    if dropped:
        report += f" ({dropped} more over the per-round cap — next round)"
    return Done(report)


# ── kit doc helper (author needs the full 2D/3D doc, not the error-scoped surface) ────
def _kit_doc_for(spec) -> str:
    from maestro.codegen.module import _kit_doc
    return _kit_doc(spec)


# ── shape dispatch ────────────────────────────────────────────────────────────
def step(shape: str, spec, run_dir, tools, fc, result) -> Outcome:
    if shape == "plan":
        return plan_step(spec, run_dir, tools, fc, result)
    if shape == "data":
        return data_step(spec, run_dir, tools, fc, result)
    if shape == "author":
        return author_step(spec, run_dir, tools, fc, result)
    if shape == "read_write":
        if not fc.started:
            return read_write_start(spec, run_dir, fc)
        return read_write_apply(spec, run_dir, tools, fc, result)
    if shape == "audit":
        return audit_step(spec, run_dir, tools, fc, result)
    raise ValueError(f"unknown fix shape {shape!r}")
