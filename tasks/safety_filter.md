# Safety Filter — Illegal-Content Prevention

**Launch gate** (legal/trust-safety). Phase 1 (input screens) and Phase 2 (output side) are both
implemented; what remains is legal input. Research notes behind the Phase 1 choices:
`tasks/safety_phase1_notes.md`.

## Policy

- **Fail closed for illegal categories.** On uncertainty in a hard-illegal category, block.
- **Illegal ≠ mature.** Dark/mature creative themes are product; the CSAM combination is the
  text-side line. The term/pattern catalog is `src/tools/safety_terms.json`.
- **Generated imagery: no explicit sexual content, full stop.** Age estimation from generated
  pixels is unreliable, so the image policy blocks all explicit renders rather than pretending to
  distinguish. A future explicit-content surface is a policy change over the same stored verdicts,
  not a rebuild — the classifier only ever reports scores.
- **Do not rely on the generation model refusing.** Local models don't; every screen is an
  external layer.

## What stands (all wired, all tested)

**Input seams** — `screen_text` at every place user text enters the system: new game, enhance,
stage edit, prompt edit, fix note, asset regenerate note (`api/routers/games.py`), and every
finalized image prompt (`tools/comfyui_tools.py:build_item_payload`, the one seam every image and
mesh job passes through).

**Post-gen image verdict** — the image worker classifies every render it ships
(`worker/safety_vision.py`, a small timm ViT from `SAFETY_MODEL_DIR`, scores attached to each
image in the job result). The control plane decides at the save-op seam
(`maestro/codegen/assets.py:render_verdict`, enforced in `asset_chain._admit`): NSFW at or over
threshold — or a render with no scores at all — is refused, the blob deleted, the manifest entry
marked `refused`, the violation recorded. A refused mesh source never reaches TRELLIS. The top-up
skips refused entries; a regenerate with a new note clears the marker.

**Artifact text gate** — `maestro/codegen/artifact_screen.py` screens the game folder's authored
text at every playable finalize, before staging. A hit HOLDS the build: status `held`, nothing
staged or snapshotted or archived, play/build/fix refused (423), the owner sees a neutral
"something went wrong — we're looking into it", and the admin panel shows the violation.

**Violations table** — every refusal persists (`db/store.py:record_violation`: user, game,
source, category, matched terms — never the flagged content). `GET /api/admin/violations` +
the admin panel table are the repeat-offender view.

## Deploy

The classifier bundle is produced once (`scripts/export_safety_model.py`, needs HF access) and
shipped to the image worker's model tree as `safety/` — the RunPod network volume for pods, the
ComfyUI models dir locally. Order matters on cutover: weights + worker image FIRST, control plane
second — the control plane refuses verdict-less renders, so old workers under a new control plane
mean every render is refused.

## Parked (needs legal input — counsel not yet consulted)

- CSAM reporting/preservation obligations: whether a refusal (nothing generated, nothing stored)
  triggers reporting duties, and what a compliant pipeline looks like if hash-matching lands.
- Hash-matching (PhotoDNA-class, access-gated): only relevant if user-uploaded images ever enter
  the pipeline; they don't today.
- Jurisdiction scope for categories beyond CSAM.
- A formal appeal path for a wrongly-held build. Today: the admin panel shows the hit, and
  releasing a held game is a manual status change by the admin.
