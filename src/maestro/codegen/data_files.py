"""Data files — model-designed, typed game DATA separated from game CODE.

Source of truth on disk per run, under `game/data/`:
  - `manifest.json`   dataset declarations: {"datasets":[{"name","fields":{...}}]}
  - `<name>.json`     the rows: a JSON array of flat objects
  - `game/data.ts`    GENERATED deterministically from the JSON — one interface + one typed const
                      per dataset. Games `import { ENEMIES } from "./data.ts"`; esbuild bundles it,
                      tsc typechecks it natively, the sim/render law is untouched.

Every row carries the implicit ENVELOPE (never declared in `fields`): `id` (required, unique
lowercase slug), `name?`, `look?` (an image-gen art prompt), `presence?` ("world"|"ui"|"both"),
`size?` ({w,h} or {w,h,d} — WORLD UNITS in 3D, PIXELS in 2D), `shape?`, `color?` and `parts?`
(a 2D COMPOUND look: sub-shapes in fractions of the entity box, so hand-drawn art is data too). The envelope
serves the PIPELINE (assets/gates); custom fields serve the game and the pipeline never interprets
them.

`size`/`shape`/`color` + `id` are the row's WHOLE VISUAL: `kit.spawnData(world, row, {x,y,z})`
builds the entity from them and binds the asset id (`mesh` in 3D, `sprite` in 2D), so the skin
stage never has to rewrite source to tag an entity — the row it came from already says what it
looks like, skinned or not.
"""

import json
import re
from pathlib import Path

from maestro.codegen.gates import game_dir

_SLUG = re.compile(r"^[a-z][a-z0-9_]*$")
_REF = re.compile(r"^ref:([a-z][a-z0-9_]*)(\[\])?$")
_BASE_TYPES = {"number", "string", "boolean", "number[]", "string[]"}
_ENVELOPE = ("id", "name", "look", "presence", "size", "shape", "color", "parts")
_SHAPES_3D = ("box", "sphere")
_SHAPES_2D = ("rect", "circle")
_PRESENCE = ("world", "ui", "both")
_TYPE_VOCAB = ("number, string, boolean, number[], string[], ref:<dataset>, ref:<dataset>[], "
               "each optionally ending in ?")
_HEADER = "// GENERATED from data/*.json — never edit by hand; change the JSON instead."


def data_dir(run_dir) -> Path:
    return game_dir(run_dir) / "data"


def data_manifest_path(run_dir) -> Path:
    return data_dir(run_dir) / "manifest.json"


def read_data_manifest(run_dir) -> dict:
    p = data_manifest_path(run_dir)
    try:
        return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}
    except json.JSONDecodeError:
        return {}


def _parse_type(t):
    """'number?' -> ('number', True); invalid vocab -> (None, optional)."""
    if not isinstance(t, str):
        return None, False
    optional = t.endswith("?")
    base = t[:-1] if optional else t
    if base in _BASE_TYPES or _REF.match(base):
        return base, optional
    return None, optional


def _jstype(v) -> str:
    if isinstance(v, bool):
        return "boolean"
    if isinstance(v, (int, float)):
        return "number"
    if isinstance(v, str):
        return "string"
    if isinstance(v, list):
        return "array"
    if isinstance(v, dict):
        return "object"
    return "null"


def _value_ok(base: str, v) -> bool:
    if base == "number":
        return isinstance(v, (int, float)) and not isinstance(v, bool)
    if base == "string":
        return isinstance(v, str)
    if base == "boolean":
        return isinstance(v, bool)
    if base == "number[]":
        return isinstance(v, list) and all(isinstance(x, (int, float)) and not isinstance(x, bool)
                                           for x in v)
    if base == "string[]":
        return isinstance(v, list) and all(isinstance(x, str) for x in v)
    m = _REF.match(base)
    if m:
        return (isinstance(v, list) and all(isinstance(x, str) for x in v)) if m.group(2) \
            else isinstance(v, str)
    return False


def _read_rows(run_dir, name: str):
    """The rows array for a dataset, or [] when the file is missing/unparseable/not an array —
    callers that must DIAGNOSE those cases go through validate_data instead."""
    p = data_dir(run_dir) / f"{name}.json"
    try:
        rows = json.loads(p.read_text(encoding="utf-8")) if p.exists() else []
    except json.JSONDecodeError:
        return []
    return rows if isinstance(rows, list) else []


def _valid_decls(manifest: dict) -> list:
    """The declared datasets that carry a valid slug name — the ones row-level checks can run on."""
    out, seen = [], set()
    for ds in (manifest.get("datasets") or []):
        if isinstance(ds, dict) and isinstance(ds.get("name"), str) and _SLUG.match(ds["name"]) \
                and ds["name"] not in seen:
            seen.add(ds["name"])
            out.append(ds)
    return out


def validate_data(run_dir) -> list:
    """Per-error actionable strings; empty list = valid. An empty datasets list is valid."""
    errors = []
    p = data_manifest_path(run_dir)
    if not p.exists():
        return ["data/manifest.json: missing"]
    try:
        manifest = json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        return [f"data/manifest.json: invalid JSON — {e}"]
    if not isinstance(manifest, dict) or not isinstance(manifest.get("datasets"), list):
        return ['data/manifest.json: must be an object {"datasets": [...]}']

    names = [ds["name"] for ds in _valid_decls(manifest)]
    seen_names = set()
    for i, ds in enumerate(manifest["datasets"]):
        if not isinstance(ds, dict) or not isinstance(ds.get("name"), str):
            errors.append(f'data/manifest.json datasets[{i}]: must be an object with "name" and "fields"')
            continue
        name = ds["name"]
        if not _SLUG.match(name):
            errors.append(f"data/manifest.json datasets[{i}]: name {name!r} must be a lowercase "
                          "slug ([a-z][a-z0-9_]*)")
            continue
        if name in seen_names:
            errors.append(f"data/manifest.json dataset '{name}': duplicate dataset name")
            continue
        seen_names.add(name)
        fields = ds.get("fields", {})
        if not isinstance(fields, dict):
            errors.append(f'data/manifest.json dataset \'{name}\': "fields" must be an object of '
                          "name → type")
            continue
        for fname, ftype in fields.items():
            if not isinstance(fname, str) or not _SLUG.match(fname):
                errors.append(f"data/manifest.json dataset '{name}': field name {fname!r} must be "
                              "a lowercase slug ([a-z][a-z0-9_]*)")
                continue
            if fname in _ENVELOPE:
                errors.append(f"data/manifest.json dataset '{name}': field '{fname}' is an "
                              "envelope field — never declare it")
                continue
            base, _ = _parse_type(ftype)
            if base is None:
                errors.append(f"data/manifest.json dataset '{name}': field '{fname}' has invalid "
                              f"type {ftype!r} — allowed: {_TYPE_VOCAB}")
                continue
            ref = _REF.match(base)
            if ref and ref.group(1) not in names:
                errors.append(f"data/manifest.json dataset '{name}': field '{fname}' refs unknown "
                              f"dataset '{ref.group(1)}'")

    # Pass 1: load every valid dataset's rows + collect ids, so ref integrity can look across datasets.
    rows_by_ds, ids_by_ds = {}, {}
    for ds in _valid_decls(manifest):
        name = ds["name"]
        p = data_dir(run_dir) / f"{name}.json"
        if not p.exists():
            errors.append(f"data/{name}.json: missing — declared in the manifest, write its rows array")
            continue
        try:
            rows = json.loads(p.read_text(encoding="utf-8"))
        except json.JSONDecodeError as e:
            errors.append(f"data/{name}.json: invalid JSON — {e}")
            continue
        if not isinstance(rows, list):
            errors.append(f"data/{name}.json: must be a JSON array of row objects")
            continue
        rows_by_ds[name] = rows
        ids_by_ds[name] = {r["id"] for r in rows
                           if isinstance(r, dict) and isinstance(r.get("id"), str)}

    # Pass 2: validate each row against the envelope + its declared fields.
    for ds in _valid_decls(manifest):
        name = ds["name"]
        if name not in rows_by_ds:
            continue
        fields = ds.get("fields") if isinstance(ds.get("fields"), dict) else {}
        declared = {f: _parse_type(t) for f, t in fields.items()
                    if isinstance(f, str) and _SLUG.match(f) and f not in _ENVELOPE
                    and _parse_type(t)[0] is not None}
        seen_ids = set()
        for i, row in enumerate(rows_by_ds[name], start=1):
            where = f"data/{name}.json row {i}"
            if not isinstance(row, dict):
                errors.append(f"{where}: must be a flat object")
                continue
            rid = row.get("id")
            if rid is None:
                errors.append(f"{where}: missing required field 'id'")
            elif not isinstance(rid, str) or not _SLUG.match(rid):
                errors.append(f"{where}: id {rid!r} must be a lowercase slug ([a-z][a-z0-9_]*)")
            else:
                where = f"{where} (id '{rid}')"
                if rid in seen_ids:
                    errors.append(f"{where}: duplicate id '{rid}'")
                seen_ids.add(rid)
            for env in ("name", "look", "shape", "color"):
                if env in row and not isinstance(row[env], str):
                    errors.append(f"{where}: field '{env}' must be string, got {_jstype(row[env])}")
            if "parts" in row:
                parts = row["parts"]
                if not isinstance(parts, list) or not all(isinstance(q, dict) for q in parts):
                    errors.append(f"{where}: parts must be an array of objects")
                else:
                    for q in parts:
                        bad = [k for k in ("dx", "dy", "w", "h")
                               if k in q and not (isinstance(q[k], (int, float))
                                                 and not isinstance(q[k], bool))]
                        if bad:
                            errors.append(f"{where}: part {', '.join(bad)} must be a number "
                                          "(a FRACTION of the entity box, 0..1)")
                        if isinstance(q.get("shape"), str) and q["shape"] not in _SHAPES_2D:
                            errors.append(f'{where}: part shape must be one of '
                                          f'{", ".join(_SHAPES_2D)}, got {q["shape"]!r}')
            if isinstance(row.get("shape"), str) and row["shape"] not in _SHAPES_3D + _SHAPES_2D:
                errors.append(f'{where}: shape must be one of '
                              f'{", ".join(_SHAPES_3D + _SHAPES_2D)}, got {row["shape"]!r}')
            if "presence" in row and row["presence"] not in _PRESENCE:
                errors.append(f'{where}: presence must be "world"|"ui"|"both", got {row["presence"]!r}')
            if "size" in row:
                sz = row["size"]
                ok = (isinstance(sz, dict) and set(sz) in ({"w", "h"}, {"w", "h", "d"})
                      and all(isinstance(v, (int, float)) and not isinstance(v, bool)
                              for v in sz.values()))
                if not ok:
                    errors.append(f'{where}: size must be {{"w","h"}} or {{"w","h","d"}} with '
                                  "number values")
            for fname, val in row.items():
                if fname in _ENVELOPE:
                    continue
                if fname not in declared:
                    errors.append(f"{where}: unknown field '{fname}' — not declared in the manifest")
                    continue
                base, _ = declared[fname]
                if not _value_ok(base, val):
                    errors.append(f"{where}: field '{fname}' must be {base}, got {_jstype(val)}")
                    continue
                ref = _REF.match(base)
                if ref and ref.group(1) in ids_by_ds:
                    targets = val if isinstance(val, list) else [val]
                    for t in targets:
                        if t not in ids_by_ds[ref.group(1)]:
                            errors.append(f"{where}: field '{fname}' ref '{t}' not found in "
                                          f"dataset '{ref.group(1)}'")
            for fname, (base, optional) in declared.items():
                if not optional and fname not in row:
                    errors.append(f"{where}: missing required field '{fname}'")
    return errors


# ── data.ts generation ────────────────────────────────────────────────────────
def _pascal(name: str) -> str:
    return "".join(part.capitalize() for part in name.split("_"))


def _ts_type(base: str) -> str:
    m = _REF.match(base)
    if m:
        return "string[]" if m.group(2) else "string"
    return base


def generate_data_ts(run_dir) -> None:
    """(Re)write game/data.ts from the JSON — deterministic, idempotent (writes only on change).
    An empty datasets list removes data.ts."""
    datasets = _valid_decls(read_data_manifest(run_dir))
    path = game_dir(run_dir) / "data.ts"
    if not datasets:
        if path.exists():
            path.unlink()
        return
    lines = [_HEADER,
             'export type Presence = "world" | "ui" | "both";',
             "export interface Size { w: number; h: number; d?: number }",
             "export interface Part { shape?: string; dx?: number; dy?: number; w?: number; "
             "h?: number; color?: string }"]
    for ds in datasets:
        name = ds["name"]
        fields = ds.get("fields") if isinstance(ds.get("fields"), dict) else {}
        iface = _pascal(name) + "Row"
        lines += ["", f"export interface {iface} {{",
                  "  id: string;",
                  "  name?: string;",
                  "  look?: string;",
                  "  presence?: Presence;",
                  "  size?: Size;",
                  "  shape?: string;",
                  "  color?: string;",
                  "  parts?: Part[];"]
        parsed = {}
        for fname, ftype in fields.items():
            base, optional = _parse_type(ftype)
            if base is None:
                continue
            parsed[fname] = (base, optional)
            lines.append(f"  {fname}{'?' if optional else ''}: {_ts_type(base)};")
        lines.append("}")
        lines.append(f"export const {name.upper()}: readonly {iface}[] = [")
        order = list(_ENVELOPE) + list(parsed)
        for row in _read_rows(run_dir, name):
            if not isinstance(row, dict):
                continue
            ordered = {k: row[k] for k in order if k in row}
            ordered.update({k: v for k, v in row.items() if k not in ordered})
            lines.append(f"  {json.dumps(ordered, ensure_ascii=False)},")
        lines.append("];")
    content = "\n".join(lines) + "\n"
    if path.exists() and path.read_text(encoding="utf-8") == content:
        return
    path.write_text(content, encoding="utf-8")


# ── prompt block ──────────────────────────────────────────────────────────────
def data_summary(run_dir) -> str:
    """Compact prompt block for authoring/fixing: per dataset the const + fields + ONE example row.
    Never the full rows (context economy). "" when the run has no data."""
    datasets = _valid_decls(read_data_manifest(run_dir))
    if not datasets:
        return ""
    out = ['# GAME DATA (import from "./data.ts" — typed, GENERATED; never redefine these '
           "tables inline)",
           "SPAWN FROM A ROW — never hand-build a row's entity: `kit.spawnData(state.world, ROW, "
           "{ x, y, z })` takes the row's size/shape/color AND binds its art (the row id), so the "
           "asset stage skins it with no code change. 2D: draw it with `kit.drawEntity(g, e)`, "
           "which prefers the sprite and falls back to the shape. `size` is already in the right "
           "units (2D pixels, 3D world units) — never scale it."]
    for ds in datasets:
        name = ds["name"]
        fields = ds.get("fields") if isinstance(ds.get("fields"), dict) else {}
        rows = _read_rows(run_dir, name)
        out.append(f"{name} ({len(rows)} rows) — const {name.upper()}: {_pascal(name)}Row[]")
        decls = ", ".join(f"{f}: {t}" for f, t in fields.items())
        out.append(f"  fields: id{', ' + decls if decls else ''}")
        example = next((r for r in rows if isinstance(r, dict)), None)
        if example is not None:
            out.append(f"  example: {json.dumps(example, ensure_ascii=False)}")
    return "\n".join(out)


# ── deterministic asset plan ──────────────────────────────────────────────────
def sprite_plan_from_data(run_dir, mode: str):
    """The asset plan the rows' `look` prompts already are. None when there is no data manifest or
    zero rows carry a look — the caller falls back to LLM planning. 2D: every look row becomes a
    sprite; 3D: only rows whose art lives in the world (presence "world"/"both") become meshes.
    Deduped by id across datasets, first wins."""
    datasets = _valid_decls(read_data_manifest(run_dir))
    if not datasets:
        return None
    looked = []
    for ds in datasets:
        looked += [r for r in _read_rows(run_dir, ds["name"])
                   if isinstance(r, dict) and isinstance(r.get("id"), str)
                   and isinstance(r.get("look"), str) and r["look"].strip()]
    if not looked:
        return None
    out, seen = [], set()
    for row in looked:
        if row["id"] in seen:
            continue
        if mode == "3d" and row.get("presence") not in ("world", "both"):
            continue
        seen.add(row["id"])
        size = row.get("size") if isinstance(row.get("size"), dict) else {}
        if mode == "3d":
            out.append({"id": row["id"], "prompt": row["look"],
                        "w": size.get("w", 1), "h": size.get("h", 1), "d": size.get("d", 1)})
        else:
            out.append({"id": row["id"], "prompt": row["look"],
                        "w": size.get("w", 32), "h": size.get("h", 32)})
    return out


def _infer_type(values: list):
    """The vocab type the ROW VALUES already are, or None when they're mixed/empty. Grounded in the
    actual data, so mapping a bad decl to it can't change what the rows mean."""
    vals = [v for v in values if v is not None]
    if not vals:
        return None
    for base in ("number", "string", "boolean", "number[]", "string[]"):
        if all(_value_ok(base, v) for v in vals):
            return base
    return None


def _flat_dict(v) -> bool:
    return isinstance(v, dict) and all(_SLUG.match(k) and not isinstance(x, (dict, list))
                                       for k, x in v.items())


def _normalize_design(datasets: list) -> None:
    """Safe monotone cleanup of a model-designed manifest BEFORE validation (the reconcile_types
    philosophy: don't drop good rows over a decl-level foot-gun). The observed local-model habits,
    each mapped to what the rows show the model MEANT — never to new information:
      - `null` row values mean ABSENT (models write null for "none") — strip them;
      - envelope fields re-declared in `fields` — strip the decls (the envelope always exists);
      - a trailing `?` on the field NAME (`"drops?": ...`) — it belongs on the type;
      - an improvised type (`{"$enum":[...]}`) — replace with the type the rows' values already are;
      - a one-level object field (`drops_coins: {min,max}`) — flatten decl + rows to
        `drops_coins_min`/`drops_coins_max` (rows are flat by law; flattening keeps the data);
      - a field the rows carry but `fields` never declares — declare it with the inferred type;
      - a decl with no surviving values anywhere — delete it;
      - a REQUIRED decl some rows omit — relax to optional (sparse rows mean "absent = default";
        the generated `?` type makes every consumer handle absence, so relaxing is safe and
        dropping six enemy rows over one sparse boolean is not).
    Anything still unresolvable is left for validate_data to reject as before."""
    for d in datasets:
        fields = d.get("fields") if isinstance(d.get("fields"), dict) else {}
        d["fields"] = fields
        rows = [r for r in (d.get("rows") if isinstance(d.get("rows"), list) else [])
                if isinstance(r, dict)]
        d["rows"] = rows
        for r in rows:
            for k in [k for k, v in r.items() if v is None]:
                del r[k]
        for fname in list(fields):
            if not isinstance(fname, str):
                continue
            stripped = fname.rstrip("?")
            if stripped != fname and stripped not in fields:
                t = fields.pop(fname)
                fields[stripped] = (t + "?") if isinstance(t, str) and not t.endswith("?") else t
        for fname in [f for f in fields if f in _ENVELOPE]:
            del fields[fname]
        row_fields = {k for r in rows for k in r if isinstance(k, str)
                      and _SLUG.match(k) and k not in _ENVELOPE}
        for fname in sorted(row_fields - set(fields)):
            fields[fname] = None   # undeclared but carried — type inferred below
        for fname, ftype in list(fields.items()):
            if _parse_type(ftype)[0] is not None:
                continue
            values = [r[fname] for r in rows if fname in r]
            inferred = _infer_type(values)
            if inferred is None and values and all(_flat_dict(v) for v in values):
                subs = sorted({k for v in values for k in v})
                for sub in subs:
                    sub_vals = [v[sub] for v in values if sub in v]
                    sub_t = _infer_type(sub_vals)
                    if sub_t:
                        opt = "?" if len(sub_vals) < len(rows) else ""
                        fields[f"{fname}_{sub}"] = sub_t + opt
                for r in rows:
                    v = r.pop(fname, None)
                    if isinstance(v, dict):
                        for sub in subs:
                            if sub in v and f"{fname}_{sub}" in fields:
                                r[f"{fname}_{sub}"] = v[sub]
                del fields[fname]
            elif inferred:
                missing = any(fname not in r for r in rows)
                fields[fname] = inferred + ("?" if missing else "")
            elif not values:
                del fields[fname]
                for r in rows:
                    r.pop(fname, None)
        for fname, ftype in list(fields.items()):
            base, optional = _parse_type(ftype)
            if base is not None and not optional and any(fname not in r for r in rows):
                fields[fname] = ftype + "?"


# ── design landing (the missing-design fix writes through here) ──────────────
_VIOL_DS = re.compile(r"data/([a-z][a-z0-9_]*)\.json|dataset '([a-z][a-z0-9_]*)'")


def offending_datasets(violations: list, declared: set) -> list:
    """The dataset each violation is ABOUT, deduped in first-seen order. Every violation string
    leads with its locus (`data/<name>.json …` or `dataset '<name>': …`), so only the FIRST name
    per violation counts — a broken-ref message also NAMES the innocent target dataset, and
    charging it too would make write_design drop both."""
    out = []
    for v in violations:
        for m in _VIOL_DS.finditer(v):
            name = m.group(1) or m.group(2)
            if name != "manifest" and name in declared:
                if name not in out:
                    out.append(name)
                break
    return out


def _land_design(run_dir, datasets: list) -> None:
    dd = data_dir(run_dir)
    dd.mkdir(parents=True, exist_ok=True)
    keep = {f"{d['name']}.json" for d in datasets} | {"manifest.json"}
    for stale in dd.glob("*.json"):
        if stale.name not in keep:
            stale.unlink()
    decls = [{"name": d["name"], "fields": d.get("fields") if isinstance(d.get("fields"), dict) else {}}
             for d in datasets]
    data_manifest_path(run_dir).write_text(json.dumps({"datasets": decls}, indent=2), encoding="utf-8")
    for d in datasets:
        rows = d.get("rows") if isinstance(d.get("rows"), list) else []
        (dd / f"{d['name']}.json").write_text(json.dumps(rows, indent=2, ensure_ascii=False),
                                              encoding="utf-8")


def write_design(run_dir, datasets) -> list:
    """Land a model-designed {"datasets":[{name, fields, rows}]} on disk: manifest (decls only) +
    one rows file per dataset. Validates what landed; a dataset that still violates is dropped
    WHOLESALE and the rest re-validated (dropping one can break another's ref), until clean or
    empty. Returns the dropped dataset names — the empty-design fallback is just dropping all."""
    dropped, kept = [], []
    seen = set()
    for d in datasets if isinstance(datasets, list) else []:
        name = d.get("name") if isinstance(d, dict) else None
        if isinstance(name, str) and _SLUG.match(name) and name not in seen:
            seen.add(name)
            kept.append(d)
        else:
            dropped.append(str(name)[:40])
    _normalize_design(kept)
    for _ in range(len(kept) + 1):
        _land_design(run_dir, kept)
        violations = validate_data(run_dir)
        if not violations:
            return dropped
        bad = offending_datasets(violations, {d["name"] for d in kept})
        if not bad:   # unattributable (manifest-shape) — wipe to the empty design
            bad = [d["name"] for d in kept]
        dropped += bad
        kept = [d for d in kept if d["name"] not in bad]
    return dropped
