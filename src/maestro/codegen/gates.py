"""The local gradient — pure-Node gates that grade a game's SIM (no browser, no critic).

A game is a FOLDER `<run_dir>/game/` of TypeScript modules: an entry `main.ts` (exports createGame)
plus system files it imports, plus `manifest.json` (the code contract). The gates:
  1. TYPECHECK with `tsc --noEmit` against the ambient kit types (runtime/engine.d.ts) — this catches
     the whole class of cross-file/type bugs (missing exports, wrong data shapes, bad arg counts)
     BEFORE the game runs, with file:line attribution.
  2. BUNDLE `main.ts` → `main.js` with esbuild (sourcemap) — the runnable artifact.
  3. Run the bundle headless / probe / render / scroll (node --enable-source-maps, so a runtime
     crash stack names the .ts SOURCE file, not the bundle).
"""

import json
import re
import shutil
import subprocess
from pathlib import Path

RUNTIME_DIR = Path(__file__).resolve().parents[3] / "runtime"
GAME_DIR = "game"
ENTRY_SRC = "main.ts"      # authored entry
ENTRY = "main.js"          # esbuild bundle (the runnable artifact)
MANIFEST = "manifest.json"
_TSC = RUNTIME_DIR / "node_modules" / ".bin" / "tsc"
_ESBUILD = RUNTIME_DIR / "node_modules" / ".bin" / "esbuild"
_ENGINE_DTS = RUNTIME_DIR / "engine.d.ts"
_TSCONFIG = {"compilerOptions": {"noEmit": True, "target": "ES2020", "module": "esnext",
                                 "moduleResolution": "bundler", "strict": False, "skipLibCheck": True,
                                 "allowImportingTsExtensions": True, "noImplicitAny": False},
             "include": ["*.ts"]}


def game_dir(run_dir) -> Path:
    return Path(run_dir) / GAME_DIR


def entry_src_path(run_dir) -> Path:
    return game_dir(run_dir) / ENTRY_SRC


def bundle_path(run_dir) -> Path:
    return game_dir(run_dir) / ENTRY


def manifest_path(run_dir) -> Path:
    return game_dir(run_dir) / MANIFEST


def read_manifest(run_dir) -> dict:
    p = manifest_path(run_dir)
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}


def game_files(run_dir) -> dict:
    """{name: source} for every authored .ts file (entry + systems). Excludes .d.ts type stubs and
    the built .js bundle — those are gate artifacts the model neither writes nor reads."""
    d = game_dir(run_dir)
    if not d.exists():
        return {}
    return {p.name: p.read_text(encoding="utf-8")
            for p in sorted(d.glob("*.ts")) if not p.name.endswith(".d.ts")}


def _run(args, timeout: int = 90, source_maps: bool = False) -> subprocess.CompletedProcess:
    cmd = ["node", "--enable-source-maps", *args] if source_maps else ["node", *args]
    return subprocess.run(cmd, cwd=RUNTIME_DIR, capture_output=True, text=True, timeout=timeout)


# ── typecheck (the contract gate) ─────────────────────────────────────────────
_TSC_ERR = re.compile(r"^([A-Za-z0-9_.-]+\.ts)\((\d+),\d+\):\s*(error TS\d+: .*)$", re.M)


def typecheck(run_dir) -> list:
    """Run `tsc --noEmit` over the game's .ts files against the kit types. Returns [(file, message)]
    per error (deduped), empty when clean. Sets up a self-contained check dir: the ambient
    engine.d.ts + a tsconfig are dropped in the game folder (gate artifacts, git/stage-ignored)."""
    d = game_dir(run_dir)
    if not entry_src_path(run_dir).exists():
        return []
    shutil.copyfile(_ENGINE_DTS, d / "engine.d.ts")
    (d / "tsconfig.json").write_text(json.dumps(_TSCONFIG), encoding="utf-8")
    try:
        p = subprocess.run([str(_TSC), "--noEmit", "-p", str(d / "tsconfig.json")],
                           cwd=d, capture_output=True, text=True, timeout=120)
    except Exception as e:
        return [(ENTRY_SRC, f"typecheck runner failed: {e}")]
    out, seen = [], set()
    for m in _TSC_ERR.finditer(p.stdout + p.stderr):
        key = (m.group(1), m.group(3))
        if m.group(1) != "engine.d.ts" and key not in seen:
            seen.add(key)
            out.append((m.group(1), f"line {m.group(2)}: {m.group(3)}"))
    return out


# ── type-contract reconciler ──────────────────────────────────────────────────
# The shared types.ts is the multi-file contract. When a consumer disagrees with it, an LLM "fixes" it
# by rewriting the whole file — dropping fields other files need, or restructuring a type wholesale
# (e.g. turning the Entity interface into a string union) → oscillation. This reconciler instead makes
# only SAFE, LOCAL, monotone edits derived from tsc's own errors — APPEND a field, DECLARE a missing
# export, RELAX a required field to optional, or turn a union used as a value into an enum. It never
# removes or restructures, so it converges to a permissive superset that every consumer typechecks
# against. Precision is traded for convergence (the validated trade for reaching a running game).
def _iter_interfaces(src: str):
    """Yield (name, body_start, body_end) for each `export interface NAME {…}` — body_end is the index
    of the interface's own closing brace, found by balancing braces so nested object-typed fields don't
    end it early."""
    for m in re.finditer(r"export\s+interface\s+([A-Za-z0-9_]+)\s*(?:<[^>]*>)?\s*\{", src):
        name = m.group(1)
        depth, i, n = 1, m.end(), len(src)
        while i < n and depth:
            c = src[i]
            if c == "{":
                depth += 1
            elif c == "}":
                depth -= 1
            i += 1
        if depth == 0:
            yield name, m.end(), i - 1


def _has_member(body: str, prop: str) -> bool:
    return re.search(rf"(?:^|[{{;\n])\s*{re.escape(prop)}\s*[?!]?\s*:", body) is not None


# TS2339/2551 (prop missing on a type) · TS2353/2561 (object literal specifies an unknown prop) —
# both mean "interface T lacks field P". TS2305/2724 — a type imported from ./types is not exported.
# TS2741 — a partial object literal omits a field T requires. TS2739 — same, listing several. TS2693/
# 2459 — a union type is used as a value (needs to be an enum).
_MISSING_PROP = re.compile(r"Property '([^']+)' does not exist on type '([^']+)'")
_LITERAL_UNKNOWN = re.compile(r"Object literal may only specify known properties, "
                              r"(?:and|but) '([^']+)' does not exist in type '([^']+)'")
_MISSING_EXPORT = re.compile(r"has no exported member(?: named)? '([^']+)'")
_REQ_MISSING_ONE = re.compile(r"Property '([^']+)' is missing in type '(\{[^']*)' but required in type '([^']+)'")
_REQ_MISSING_MANY = re.compile(r"Type '([^']+)' is missing the following properties from type "
                               r"'([^']+)': (.+)")
_USED_AS_VALUE = re.compile(r"'([^']+)' only refers to a type, but is being used as a value")
_IDENT = re.compile(r"[A-Za-z_]\w*")


def _append_field(types_path, typ: str, prop: str) -> bool:
    src = types_path.read_text(encoding="utf-8")
    target = next(((s, e) for name, s, e in _iter_interfaces(src) if name == typ), None)
    if target is None:
        return False
    body_start, body_end = target
    if _has_member(src[body_start:body_end], prop):
        return False
    newline = "\n" if not src[:body_end].endswith("\n") else ""
    types_path.write_text(src[:body_end] + newline + f"  {prop}?: any;\n" + src[body_end:], encoding="utf-8")
    return True


# The ambient kit globals (from engine.d.ts). A file importing one of these from ./types.ts is a
# CALLER bug (they're globals — use directly, never import) — declaring `export type Kit = any` to
# satisfy the import masks the real type with `any` and launders the bad import. Skip these; the
# authority-guided LLM removes the import instead.
_KIT_AMBIENT = {"Kit", "Entity", "World", "Input", "DrawApi", "Camera", "Vec2", "GameObject",
                "Config", "Rect", "Tilemap"}


def _exported_elsewhere(sources: dict, name: str) -> bool:
    """True if a NON-types.ts game file already exports `name` (a function/type/const living in its own
    system file). A missing-export error for such a name is a wrong IMPORT PATH, not a missing shared
    type — declaring a stub in types.ts hides the real symbol and doesn't fix the caller."""
    for fname, src in sources.items():
        if fname == "types.ts":
            continue
        if re.search(rf"export\s+(?:async\s+)?(?:interface|type|enum|const|let|var|function|class)\s+{re.escape(name)}\b", src) \
           or re.search(rf"export\s*\{{[^}}]*\b{re.escape(name)}\b[^}}]*\}}", src):
            return True
    return False


def _declare_export(types_path, name: str) -> bool:
    src = types_path.read_text(encoding="utf-8")
    if re.search(rf"export\s+(?:interface|type|enum|const)\s+{re.escape(name)}\b", src):
        return False
    sep = "" if src.endswith("\n") else "\n"
    types_path.write_text(src + sep + f"export type {name} = any;\n", encoding="utf-8")
    return True


def _relax_field(types_path, typ: str, prop: str) -> bool:
    """Make a REQUIRED field optional (`p:` → `p?:`) in a types.ts interface — so a partial literal
    that omits it typechecks. Only touches an existing required field of an interface we own."""
    src = types_path.read_text(encoding="utf-8")
    target = next(((s, e) for name, s, e in _iter_interfaces(src) if name == typ), None)
    if target is None:
        return False
    body_start, body_end = target
    body = src[body_start:body_end]
    new_body, n = re.subn(rf"(^|[{{;\n])(\s*{re.escape(prop)})\s*:", r"\1\2?:", body, count=1)
    if not n or f"{prop}?:" not in new_body:
        return False
    types_path.write_text(src[:body_start] + new_body + src[body_end:], encoding="utf-8")
    return True


def _convert_union_to_enum(types_path, typ: str, sources: dict) -> bool:
    """Turn `export type X = 'a' | 'b'` into `export enum X { … }` when X is used as a value (X.Member).
    Members come from the actual `X.Name` usages across the game; each member keeps its runtime value
    by matching a union literal case-insensitively (else the lowercased name)."""
    src = types_path.read_text(encoding="utf-8")
    m = re.search(rf"export\s+type\s+{re.escape(typ)}\s*=\s*([^;]+);", src)
    if not m:
        return False
    values = re.findall(r"['\"]([^'\"]+)['\"]", m.group(1))
    used = set()
    for body in sources.values():
        used.update(re.findall(rf"\b{re.escape(typ)}\.([A-Za-z_]\w*)", body))
    if not used:
        return False
    members = []
    for name in sorted(used):
        val = next((v for v in values if v.lower() == name.lower()
                    or v.replace("_", "").lower() == name.lower()), name.lower())
        members.append(f'{name} = "{val}"')
    enum = f"export enum {typ} {{ {', '.join(members)} }}"
    types_path.write_text(src[:m.start()] + enum + src[m.end():], encoding="utf-8")
    return True


def _reconcile_pass(types_path, errors, sources, seen, include_fields: bool = True) -> list:
    """One sweep of the safe monotone transforms over the current tsc errors, in order — enum-ify a
    union used as a value, declare a missing export, append a missing field, relax an over-strict
    required field. Each is idempotent and de-duped via `seen`. Returns the changes it made.

    `include_fields=False` DROPS the append-a-missing-field transform. Appending `health?: any` to a
    type when the caller meant the existing `hp` field greens tsc while laundering a hallucination
    into the contract as a permanent, unused field. The contract-mismatch fix class disables it so a
    field mismatch is resolved by the authority-guided LLM (reconcile the CALLER to the real field)
    instead — enum/export/relax stay on (genuinely additive, not hallucination-prone)."""
    changes = []

    def once(key, kind, fn):
        if key in seen:
            return
        seen.add(key)
        if fn():
            changes.append((kind, key))

    for msg in errors:                                              # 1. union → enum (structural, first)
        u = _USED_AS_VALUE.search(msg)
        if u:
            once(u.group(1), "enum", lambda t=u.group(1): _convert_union_to_enum(types_path, t, sources))
    for msg in errors:                                              # 2. declare GENUINELY-missing shared exports
        e = _MISSING_EXPORT.search(msg)
        # Skip an ambient kit type or a name a sibling file already exports — both are caller bugs
        # (bad import), not missing shared types; declaring an `any` stub would launder them.
        if e and e.group(1) not in _KIT_AMBIENT and not _exported_elsewhere(sources, e.group(1)):
            once(e.group(1), "export", lambda n=e.group(1): _declare_export(types_path, n))
    for msg in errors if include_fields else []:                   # 3. append missing fields (opt-out)
        p = _MISSING_PROP.search(msg) or _LITERAL_UNKNOWN.search(msg)
        if p:
            once((p.group(2), p.group(1)), "field",
                 lambda t=p.group(2), f=p.group(1): _append_field(types_path, t, f))
    for msg in errors:                                              # 4. relax over-strict required
        one = _REQ_MISSING_ONE.search(msg)
        if one:
            once((one.group(3), one.group(1)), "relax",
                 lambda t=one.group(3), f=one.group(1): _relax_field(types_path, t, f))
            continue
        many = _REQ_MISSING_MANY.search(msg)
        if many and many.group(1).lstrip().startswith("{"):         # source is a partial literal, not a wrong named type
            typ, props = many.group(2), _IDENT.findall(many.group(3).split(" and ")[0])
            for prop in props:
                once((typ, prop, "relax"), "relax",
                     lambda t=typ, f=prop: _relax_field(types_path, t, f))
    return changes


def reconcile_types(run_dir, include_fields: bool = True) -> dict:
    """Deterministic superset reconciler for the shared `types.ts` contract. Iterates the safe monotone
    transforms — enum-ify unions used as values, declare missing exports, append missing fields, relax
    over-strict required fields — to its OWN fixpoint in a single call (relaxing one required field
    reveals the next; declaring a type reveals its field gaps), so ONE loop step clears the whole
    contract instead of one edit per step. Never removes or restructures, so it converges. A relax is
    applied ONLY when the source is a partial object literal (`{…}`); a NAMED wrong type passed where an
    interface is expected is a real call bug left for the fix loop. `include_fields=False` drops the
    field-append transform (see _reconcile_pass) — the contract-mismatch class routes field mismatches
    to the authority-guided LLM instead of laundering them into the type. Returns {changes, count}."""
    types_path = game_dir(run_dir) / "types.ts"
    if not typecheck(run_dir) or not types_path.exists():
        return {"changes": [], "count": 0}
    sources = {p.name: p.read_text(encoding="utf-8") for p in game_dir(run_dir).glob("*.ts")}
    changes, seen = [], set()
    for _ in range(40):                                             # fixpoint; bounded against any pathological non-convergence
        made = _reconcile_pass(types_path, [m for _, m in typecheck(run_dir)], sources, seen,
                               include_fields=include_fields)
        if not made:
            break
        changes += made
    return {"changes": changes, "count": len(changes)}


def build_bundle(run_dir) -> dict:
    """esbuild main.ts (+ its imports) → main.js with a sourcemap. Returns {ok} or {ok:False,error}.
    Cheap (~1ms); the run gates call it so they always execute the current source."""
    entry, bundle = entry_src_path(run_dir), bundle_path(run_dir)
    if not entry.exists():
        return {"ok": False, "error": f"{ENTRY_SRC} not written yet"}
    try:
        p = subprocess.run([str(_ESBUILD), str(entry), "--bundle", "--format=esm",
                            "--sourcemap=inline", f"--outfile={bundle}"],
                           cwd=game_dir(run_dir), capture_output=True, text=True, timeout=60)
    except Exception as e:
        return {"ok": False, "error": f"bundle runner failed: {e}"}
    return {"ok": True} if p.returncode == 0 else {"ok": False, "error": (p.stderr or p.stdout)[-500:]}


def stage_for_play(run_dir, slug: str) -> str:
    """Build the bundle and copy it into runtime/games/<slug>/main.js for the browser harness (the
    bundle inlines the game's imports, so it's self-contained). Returns the play URL query."""
    build_bundle(run_dir)
    dst = RUNTIME_DIR / "games" / slug
    dst.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(bundle_path(run_dir), dst / ENTRY)
    manifest = game_dir(run_dir) / "assets.json"   # the skin, if this run has been through the assets stage
    if manifest.exists():
        shutil.copyfile(manifest, dst / "assets.json")
        src_assets = game_dir(run_dir) / "assets"
        if src_assets.exists():
            shutil.copytree(src_assets, dst / "assets", dirs_exist_ok=True)
    return f"index.html?game={slug}"


def extract_code(text: str) -> str:
    """Pull the ```ts/js block out of a model reply (a fenced block, not a tool-call arg). Falls back
    to the whole reply when unfenced."""
    m = re.search(r"```(?:ts|typescript|js|javascript)?\s*\n(.*?)```", text, re.S)
    return (m.group(1) if m else text).strip()


# ── run gates (on the bundle) ─────────────────────────────────────────────────
def run_headless(run_dir, frames: int = 900) -> dict:
    """Build then step the sim `frames` frames in pure Node. `{"ok": True}` = ran/resolved clean;
    else the crash/divergence (stack names the .ts source via the sourcemap) the fix feeds back."""
    b = build_bundle(run_dir)
    if not b.get("ok"):
        return {"ok": False, "phase": "build", "error": b.get("error", "bundle failed")}
    try:
        p = _run(["headless.mjs", str(bundle_path(run_dir)), str(frames)], source_maps=True)
    except subprocess.TimeoutExpired:
        return {"ok": False, "phase": "timeout",
                "error": f"sim did not finish {frames} frames in time (likely an infinite loop)"}
    try:
        return json.loads(p.stdout.strip().splitlines()[-1])
    except Exception:
        return {"ok": False, "phase": "runner", "error": (p.stdout + p.stderr)[-800:]}


def _run_violation_gate(run_dir, runner: str, hint: str) -> dict:
    b = build_bundle(run_dir)
    if not b.get("ok"):
        return {"ok": False, "violations": [{"kind": "build", "detail": b.get("error", "bundle failed")}]}
    try:
        p = _run([runner, str(bundle_path(run_dir))], source_maps=True)
    except subprocess.TimeoutExpired:
        return {"ok": False, "violations": [{"kind": "timeout", "detail": hint}]}
    try:
        return json.loads(p.stdout)
    except Exception:
        return {"ok": False, "violations": [{"kind": "runner", "detail": (p.stdout + p.stderr)[-400:]}]}


def run_probe(run_dir) -> dict:
    return _run_violation_gate(run_dir, "probe.mjs",
                               "probe did not finish (likely an infinite loop in update)")


def run_render(run_dir) -> dict:
    return _run_violation_gate(run_dir, "render.mjs",
                               "render did not finish (likely an infinite loop in draw)")


def run_scroll(run_dir) -> dict:
    return _run_violation_gate(run_dir, "scroll.mjs",
                               "scroll check did not finish (likely an infinite loop in update)")
