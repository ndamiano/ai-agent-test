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
from dataclasses import dataclass
from typing import List, Optional, Union

from llm_clients.message_builder import MessageBuilder
from maestro.codegen import data_files, interfaces
from maestro.codegen.fix_classes import (
    classify,
    strip_dead_creategame,
    strip_unplanned_imports,
)
from maestro.codegen.gates import extract_code, game_files, manifest_path
from maestro.codegen.module import (
    _CODE_MAX_TOKENS,
    _DATA_MAX_TOKENS,
    _IFACE_MAX_TOKENS,
    _REVIEW_MAX_TOKENS,
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
    _manifest_files,
    _seeded_block,
    _sibling_lines,
)
from maestro.services import parse_args, salvage_tool_call


# ── step outcomes ─────────────────────────────────────────────────────────────
@dataclass
class Infer:
    """Suspend the fix here: enqueue one `llm` job with these messages, die, resume on completion."""
    messages: List[dict]
    schemas: List[dict]
    max_tokens: int
    # "none" is the floor for every build turn. Passing None instead reaches the connector as an
    # explicit "let the model pick", which skips the enable_thinking switch — a measured authoring
    # turn then spent 16000 tokens reasoning and never called `write`.
    reasoning: Optional[str] = "none"
    report: Optional[str] = None      # progress line emitted when this turn is enqueued


@dataclass
class Done:
    """The fix is finished — return to the outer gate sweep."""
    report: str


Outcome = Union[Infer, Done]


def _content(result) -> str:
    return ((result.get("choices") or [{}])[0].get("message", {}) or {}).get("content", "") or ""


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


# ── interfaces (the architecture, one turn) ───────────────────────────────────
def interfaces_step(spec, run_dir, tools, fc, result) -> Outcome:
    if not fc.started:
        fc.started = True
        return _interfaces_request(spec, run_dir, report="declaring the architecture")
    try:
        iface = interfaces.normalize(_json_from(_content(result)))
    except Exception:
        iface = interfaces.normalize({})
    # An architecture with no functions is not a smaller architecture, it is no game: nothing to
    # author, no contracts to check. Leave it unwritten so `interfaced` stays red and the outer loop
    # runs the turn again — bounded by the step cap and the stall detector, which fail the build
    # rather than shipping one with no contracts.
    if not iface["functions"]:
        return Done("the architecture came back empty — declaring it again")
    corrected = interfaces.enforce_kit_contract(run_dir, iface)
    interfaces.save(run_dir, iface)
    # state.ts first: manifest_from drops GENERATED files, so it must be on disk before the manifest
    # is derived or the model would be asked to author it.
    interfaces.generate_state_ts(run_dir, iface)
    manifest = interfaces.manifest_from(iface, run_dir, _hook_exports(spec))
    manifest_path(run_dir).parent.mkdir(parents=True, exist_ok=True)
    manifest_path(run_dir).write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return Done(f"declared {len(iface['state'])} state field(s), {len(iface['functions'])} "
                f"function(s) across {len(manifest['files'])} file(s)"
                + (f"; corrected {len(corrected)} against the kit: " + "; ".join(corrected[:4])
                   if corrected else ""))


def _interfaces_request(spec, run_dir, report: str) -> Infer:
    system = (_PROMPTS / "design_interfaces.txt").read_text(encoding="utf-8")
    user = (f"{_design_block(spec)}{_seeded_block(run_dir)}"
            f"{interfaces.hooks_block(run_dir, spec)}\n\n"
            "Declare the architecture. Output ONLY one ```json block.")
    return Infer(MessageBuilder(system).add_user(user).build(), [], _IFACE_MAX_TOKENS,
                 report=report)


# ── review (rounds of find-then-patch over the architecture) ──────────────────
_REVIEW_ROUNDS = 3
_REVIEW_BATCH = 2


def review_step(spec, run_dir, tools, fc, result) -> Outcome:
    """The model reviews the declarations it just wrote, patching by op rather than re-emitting the
    architecture. Converges when a whole round reports nothing new; `_REVIEW_ROUNDS` is the backstop
    for a reviewer that keeps inventing work, not the intended exit."""
    iface = interfaces.load(run_dir) or {"state": [], "functions": [], "invariants": []}
    if not fc.started:
        fc.started = True
        fc.rnd = 1
        return _review_find_infer(iface, fc)
    if fc.mode == "patch":
        return _review_patch_apply(run_dir, iface, fc, result)
    return _review_find_apply(run_dir, iface, fc, result)


def _review_find_infer(iface, fc) -> Infer:
    fc.mode = "find"
    parts = list(interfaces.slices(iface))
    sl = parts[fc.slice_idx]
    system = (_PROMPTS / "review_find.txt").read_text(encoding="utf-8")
    body = [f"# ARCHITECTURE\n```json\n{json.dumps(sl, indent=1, ensure_ascii=False)}\n```"]
    if len(parts) > 1:
        body.append(f"The state table and invariants above are COMPLETE. Only batch "
                    f"{fc.slice_idx + 1} of {len(parts)} of the functions is shown. Judge these "
                    f"functions against the full state table; do not report a function as missing "
                    f"merely because it is not in this batch.")
    if fc.seen:
        body.append("Problems already found and corrected in an earlier pass. They are fixed; do "
                    "not report them again:\n"
                    + "\n".join(f"  - {t}" for t in fc.seen[-20:]))
    return Infer(MessageBuilder(system).add_user("\n\n".join(body)).build(), [], _REVIEW_MAX_TOKENS,
                 report=f"reviewing the architecture (round {fc.rnd}, batch {fc.slice_idx + 1}/{len(parts)})")


def _review_find_apply(run_dir, iface, fc, result) -> Outcome:
    try:
        fc.found += [p for p in (_json_from(_content(result)).get("problems_found") or [])]
    except Exception:
        pass
    fc.slice_idx += 1
    if fc.slice_idx < len(interfaces.slices(iface)):
        return _review_find_infer(iface, fc)

    fresh = []
    for p in fc.found:
        key = interfaces.problem_key(p)
        if key and key not in fc.seen:
            fc.seen.append(key)
            fresh.append(p)
    fc.slice_idx, fc.found = 0, []
    if not fresh:
        return _review_done(run_dir, iface, fc, f"architecture clean after {fc.rnd} round(s)")
    fc.fresh, fc.patch_idx = fresh, 0
    return _review_patch_infer(iface, fc)


def _review_patch_infer(iface, fc) -> Infer:
    fc.mode = "patch"
    chunk = fc.fresh[fc.patch_idx:fc.patch_idx + _REVIEW_BATCH]
    system = (_PROMPTS / "review_patch.txt").read_text(encoding="utf-8")
    body = ["# PROBLEMS TO FIX\n" + "\n".join(f"- {json.dumps(p, ensure_ascii=False)}" for p in chunk),
            f"# FULL ARCHITECTURE\n```json\n{json.dumps(iface, indent=1, ensure_ascii=False)}\n```"]
    if fc.feedback:
        body.append(fc.feedback)
    return Infer(MessageBuilder(system).add_user("\n\n".join(body)).build(), [], _REVIEW_MAX_TOKENS,
                 report=f"patching the architecture (round {fc.rnd}, "
                        f"{fc.patch_idx + len(chunk)}/{len(fc.fresh)})")


def _review_patch_apply(run_dir, iface, fc, result) -> Outcome:
    try:
        patches = _json_from(_content(result)).get("patches") or []
    except Exception:
        patches = []
    errs = interfaces.apply_patches(iface, patches, dry=True)
    if errs and not fc.patch_retry:
        fc.patch_retry = 1
        fc.feedback = ("Your previous patches could not be applied:\n"
                       + "\n".join(f"- {e}" for e in errs) + "\nSend corrected patches.")
        return _review_patch_infer(iface, fc)
    rejected = interfaces.apply_patches(iface, patches)
    interfaces.save(run_dir, iface)
    interfaces.log_review(run_dir, fc.rnd, fc.fresh[fc.patch_idx:fc.patch_idx + _REVIEW_BATCH],
                          patches, rejected)
    fc.patch_retry, fc.feedback = 0, ""
    fc.patch_idx += _REVIEW_BATCH
    if fc.patch_idx < len(fc.fresh):
        return _review_patch_infer(iface, fc)
    fc.fresh, fc.patch_idx = [], 0
    fc.rnd += 1
    if fc.rnd > _REVIEW_ROUNDS:
        return _review_done(run_dir, iface, fc,
                            f"review hit the {_REVIEW_ROUNDS}-round cap "
                            f"({len(fc.seen)} problem(s) corrected)")
    return _review_find_infer(iface, fc)


def _review_done(run_dir, iface, fc, report: str) -> Done:
    iface["reviewed"] = True
    interfaces.save(run_dir, iface)
    interfaces.generate_state_ts(run_dir, iface)   # the review patches state; the type follows it
    return Done(report)


# ── amend (rule on a conformance violation, then fix whichever side was wrong) ─
def amend_step(spec, run_dir, tools, fc, result) -> Outcome:
    """A conformance violation is the code disagreeing with a contract the model wrote before it knew
    what the code would look like, so the first turn RULES on which side is wrong. `contract` patches
    interfaces.json and the fix is over; `code` falls through to the read→edit subloop."""
    if fc.mode == "fix":
        return read_write_apply(spec, run_dir, tools, fc, result)
    if not fc.started:
        fc.started = True
        return _amend_request(spec, run_dir, fc)

    try:
        ruling = _json_from(_content(result))
    except Exception:
        ruling = {}
    if str(ruling.get("verdict", "")).lower() == "contract":
        iface = interfaces.load(run_dir) or {}
        patches = ruling.get("patches") or []
        rejected = interfaces.apply_patches(iface, patches)
        interfaces.save(run_dir, iface)
        # The GENERATED state type follows the declaration — a ruling on a shape is inert until it does.
        interfaces.generate_state_ts(run_dir, iface)
        landed = len(patches) - len(rejected)
        if landed:
            return Done(f"amended the contract ({landed} op(s)): {str(ruling.get('reason', ''))[:120]}")
    fc.mode = "fix"
    return read_write_start(spec, run_dir, fc)


def _amend_request(spec, run_dir, fc) -> Infer:
    from maestro.codegen.build_state import error_from_dict
    error = error_from_dict(fc.error)
    iface = interfaces.load(run_dir) or {}
    files = game_files(run_dir)
    body = (files.get(error.path) or "") if error.path else ""
    system = (_PROMPTS / "amend_contract.txt").read_text(encoding="utf-8")
    parts = [
        _design_block(spec),
        f"# THE ARCHITECTURE YOU DECLARED\n```json\n"
        f"{json.dumps(iface, indent=1, ensure_ascii=False)}\n```",
        f"# THE CONFLICT\n{error.message}",
    ]
    if body:
        parts.append(f"# YOUR IMPLEMENTATION: {error.path}\n```ts\n{body}\n```")
    parts.append("Rule on which side is wrong. Output ONLY one ```json block.")
    return Infer(MessageBuilder(system).add_user("\n\n".join(parts)).build(), [], _REVIEW_MAX_TOKENS,
                 report="ruling on a contract conflict")


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
        interfaces.interfaces_block(interfaces.load(run_dir)),
        interfaces.state_ts_block(run_dir),
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
    """The file list, the failure, and the tools. Nothing else — the kit surface, the state contract
    and the data tables are files the fix reads when it wants them."""
    from maestro.codegen.build_state import error_from_dict
    error = error_from_dict(fc.error)
    cls = classify(error)
    # Everything readable, not just the manifest's model-authored files: the GENERATED ones and
    # engine.d.ts are what a fix traces through.
    filelist = ", ".join(sorted(set(game_files(run_dir)) | {"engine.d.ts"}))
    system = (_PROMPTS / "fix_loop.txt").read_text(encoding="utf-8")
    user = "\n\n".join(p for p in [
        f"# FILES (read any of these)\n{filelist}",
        f"# FAILING GATE\n{error.message}",
        cls.authority(spec, run_dir, error) if cls.authority else "",
        cls.directive or "",
    ] if p)
    fc.system = system
    fc.history = [{"role": "user", "content": user}]
    fc.started = True
    return _read_write_infer(fc)


def _read_write_infer(fc) -> Infer:
    schemas = _fix_schemas(fc.escalate, fc.nreads)
    msgs = MessageBuilder(fc.system).extend(fc.history).build()
    return Infer(msgs, schemas, _CODE_MAX_TOKENS,
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
    """One audit round: every spec claim judged by a read→verdict subloop that must cite the traced
    path. A claim with no verdict inside its turn cap is SKIPPED, never a finding — an audit failure
    must not strand a green build."""
    from maestro.codegen import audit
    claims = audit.claims_of(spec)
    if not claims:
        return Done("audit: spec enumerates no claims — passing")
    if not fc.started:
        fc.started = True
        return _audit_claim_start(audit, spec, run_dir, claims, fc)

    message = (result.get("choices") or [{}])[0].get("message", {}) or {}
    content = message.get("content", "") or ""
    if len(content) > 2000:
        content = "[…truncated…]\n" + content[-2000:]
    calls = [tc for tc in (message.get("tool_calls") or []) if tc.get("function", {}).get("name")]
    fc.turn += 1

    # A model that answers with the JSON in content instead still lands via parse_verdict below.
    committed = next((tc for tc in calls if tc["function"]["name"] == "verdict"), None)
    if committed:
        content = json.dumps(parse_args(committed["function"].get("arguments")))

    tcs = [tc for tc in calls if tc["function"]["name"] == "read_file"]
    if tcs and not committed and fc.turn < audit.CLAIM_TURN_CAP:
        fc.history.append({"role": "assistant", "content": content, "tool_calls": tcs})
        for tc in tcs:
            args = parse_args(tc["function"].get("arguments"))
            read_args = {"file": args.get("file", "")}
            if args.get("offset") is not None:
                read_args["offset"] = args["offset"]
            if args.get("limit") is not None:
                read_args["limit"] = args["limit"]
            res = tools["read_file"](**read_args)
            fc.nreads += 1
            fc.history.append({"role": "tool", "tool_call_id": tc.get("id"),
                               "content": json.dumps(res)})
        return _audit_claim_infer(audit, claims, fc)

    verdict = audit.parse_verdict(content)
    if verdict is None and fc.turn < audit.CLAIM_TURN_CAP:
        fc.history.append({"role": "assistant", "content": content})
        fc.history.append({"role": "user",
                           "content": "Call read_file to trace further, or call verdict to commit."})
        return _audit_claim_infer(audit, claims, fc)

    claim = claims[fc.claim_idx]
    if verdict is None:
        fc.verdicts.append({"claim": claim, "status": "skipped", "evidence": ""})
    else:
        status = str(verdict.get("status", "")).lower()
        fc.verdicts.append({"claim": claim, "status": status,
                            "evidence": verdict.get("evidence", "")})
        if status == "delivered":
            fc.delivered.append(claim)
        elif audit.is_failed(verdict):
            fc.findings.append({"claim": claim, "note": audit.note_for(claim, verdict)})
    fc.claim_idx += 1
    if fc.claim_idx < len(claims):
        return _audit_claim_start(audit, spec, run_dir, claims, fc)

    audit.log_verdicts(run_dir, fc.verdicts)
    dropped = max(0, len(fc.findings) - audit.MAX_FINDINGS_PER_ROUND)
    fc.findings = fc.findings[:audit.MAX_FINDINGS_PER_ROUND]
    skipped = sum(1 for v in fc.verdicts if v["status"] == "skipped")
    report = (f"audit: {len(fc.delivered)}/{len(claims)} delivered, "
              f"{len(fc.findings)} finding(s) queued")
    if skipped:
        report += f", {skipped} skipped"
    if dropped:
        report += f" ({dropped} more over the per-round cap — next round)"
    return Done(report)


def _audit_claim_start(audit, spec, run_dir, claims, fc) -> Infer:
    claim = claims[fc.claim_idx]
    system, user = audit.claim_prompt(spec, run_dir, claim, anchored=claim in fc.anchors)
    fc.system = system
    fc.history = [{"role": "user", "content": user}]
    fc.turn = 0
    fc.nreads = 0
    return _audit_claim_infer(audit, claims, fc)


def _audit_claim_infer(audit, claims, fc) -> Infer:
    msgs = MessageBuilder(fc.system).extend(fc.history).build()
    return Infer(msgs, audit.read_schemas(fc.nreads), audit._AUDIT_MAX_TOKENS,
                 report=f"audit claim {fc.claim_idx + 1}/{len(claims)} (turn {fc.turn + 1})")


# ── kit doc helper (author needs the full 2D/3D doc, not the error-scoped surface) ────
def _kit_doc_for(spec) -> str:
    from maestro.codegen.module import _kit_doc
    return _kit_doc(spec)


# ── shape dispatch ────────────────────────────────────────────────────────────
def step(shape: str, spec, run_dir, tools, fc, result) -> Outcome:
    if shape == "interfaces":
        return interfaces_step(spec, run_dir, tools, fc, result)
    if shape == "review":
        return review_step(spec, run_dir, tools, fc, result)
    if shape == "amend":
        return amend_step(spec, run_dir, tools, fc, result)
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
