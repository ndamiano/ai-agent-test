# debug/ — prompt-iteration harness

One driver (`harness.py`) for hand-climbing any build mode's prompt against the real local model,
without running a full build. It reproduces ONE production LLM call for a chosen mode — same system
prompt, same rendered context, same tool schema — so what you see is what the loop sees.

**Run with the venv active, from the repo root** (no PYTHONPATH needed — `_bootstrap.py` puts
`src/` on the path):

```bash
source venv/bin/activate
python debug/harness.py <mode> [N]        # N fresh calls from the LIVE prompt (default 1)
python debug/harness.py <mode> dump       # write the exact assembled system+user to work/<mode>_*.txt
python debug/harness.py <mode> send N     # replay work/<mode>_system.txt + _user.txt VERBATIM, N times
python debug/harness.py reseed            # (re)propose+cache the spec from work/request.txt
```

`mode` ∈ `premise`, `outline`, `asset`, `nodes`.

Edit the mode's prompt under `src/maestro/prompts/` (e.g. `mode_premise.txt`, `write_node.txt`),
rerun. `dump` lets you hand-edit the fully-assembled prompt (includes resolved, context rendered);
`send` replays your edit untouched, so you can poke the exact string the model sees. Once dialed in,
port wins back into the real prompt file (+ partials).

## How a mode is wired
A mode's only inputs are the spec + its upstream components. The harness seeds a fresh run with the
spec + every component before the target in dep order (copied from `fixtures/`), so `build_context`
resolves to the target as the earliest unwritten component. You iterate ONLY that mode's prompt.

The spec comes from `work/seed_spec.json` if present (see `reseed`), else `fixtures/spec.json`.
To iterate on a different request: edit `work/request.txt`, then `reseed`. (Note: `reseed` only
changes the spec; the `fixtures/` upstream components still match the original request, so for
downstream modes — outline/nodes — reseeding to a very different premise will mismatch. Fine for
premise; for downstream, also refresh the relevant fixture.)

## Adding a mode
One line in `MODES` (component id + tool name + a show fn). Add a `show_*` only if the output shape
is new; reuse `show_json` otherwise. Subloop modes (nodes/places) are detected automatically — the
`target` injection + graph projector are handled in `assemble()`. `places`/`matches` need fixtures
for their preset (pnc/card) before they'll resolve; drop a finished run's components into `fixtures/`.

## Layout
- `harness.py` — the driver + `MODES` registry.
- `_bootstrap.py` — path setup + `register_all()`. Import-first.
- `fixtures/` *(gitignored — generated)* — frozen seed components. Regenerate from any finished run:
  `cp <working_dir>/runs/<run_id>/{spec,premise,asset_manifest,outline}.json debug/fixtures/`
  (the current set is from run `ec603cd830d5`). Self-contained; not dependent on a run dir surviving.
- `work/` *(gitignored)* — iteration scratch: cached spec, request, and the `<mode>_system.txt` /
  `<mode>_user.txt` you hand-edit and replay with `send`.

## Notes
- Reasoning is OFF by default (matches the production first call). Output rescues prose-JSON (the
  model dumping content as text instead of a tool call) so you still see the result.
- `send` replays your edited files untouched — if your edit invites a multi-node dump or trips the
  small-model no-tool-call failure, you'll see exactly that (real model behavior).
- Run dirs land in the configured `working_directory` (`/home/nick/output/runs/`), prefixed with the
  mode (`premise_`, `nodes_`, …). Disposable; delete periodically.
