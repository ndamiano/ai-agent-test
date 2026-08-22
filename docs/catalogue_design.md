# The asset catalogue — design

Status: DESIGN, not built. Gated on the trial at the bottom.

A build asks for art through `generate_media(id, prompt, kind)` and worldgen asks for scenery
through a `RegionalObject`. Both describe a thing in prose and get a file. The catalogue sits
behind that description: a store of renders and meshes we already made and a human already kept,
and a matcher that answers the description from the store when one of them IS the thing, and
generates when none is. The model asking never learns the catalogue exists — the tool, its schema
and its answer are unchanged — so the build loop pays nothing for it on any turn.

Two things it buys. Compute: a sprite hit saves a render, a mesh hit saves an image plus a TRELLIS
reconstruction. Quality: a kept asset is a known-good one, and scattered scenery — the bushes,
rocks, fences, and crates that every world asks for and that reconstruction does worst — is the
bulk of what gets asked for.

The idea is retrieval behind an LLM's prose description (Holodeck, Yang et al. 2024): the LLM
describes and places, the system retrieves. The difference here is that the store is our own
output and only a human fills it.

---

## Scope

Every `kind` — `sprite`, `tile`, `scene`, `mesh`. One rule for all of them; scenes will hit
rarely and nothing is lost by leaving them in.

Every object — no split between "scatter" and "hero". A two-storey inn is a two-storey building
restyled; a lighthouse keeper's cracked lantern is a NONE. The vision step decides, and its prompt
is biased toward NONE.

## Corpus

Only what our own pipeline generated, in a real run. No third-party packs: the dependency is
one we would carry forever, the licence is a legal line we have not written, and the style would
never match what the pipeline draws beside it.

Every entry carries its provenance: source run and asset id, the verbatim prompt, kind, the image
model that drew it, the safety verdict and score it arrived with, who promoted it and when.

The catalogue is empty on day one and grows from builds. The first measurement is how fast the
hit rate climbs, not what it is.

## Storage

- **Files** in the archive bucket under `catalogue/<kind>/<entry>.<ext>`, a thumbnail beside each
  under `catalogue/thumbs/`, and for a mesh the subject drawing it was reconstructed from under
  `catalogue/drawings/` — a restyle conditions on it. Promotion COPIES out of the run dir, so archiving or deleting a run
  never loses an entry, and a game keeps its own copy in `assets/`, so retiring an entry never
  breaks a shipped game.
- **Index** in one control-plane table: id, kind, prompt, tags, style family, real size (metres,
  meshes), triangle count, source run and asset, safety score, status (`active` | `retired`),
  hit and miss counts, promoted-by/at, retired-at. Tags are derived once at promotion — the
  tokenised prompt plus one category noun from a single llm call — so matching is plain SQL and
  token overlap, no embedding model, no new dependency.
- **Workers never see the store.** A hit is served by the control plane, which copies the file
  into the run dir itself. Pods keep the rule that they talk to nothing after provisioning.

## Promotion — admin only, one click, at the moment of looking

Nothing enters on its own. The admin panel's catalogue tab shows each finished game's assets as a
strip — thumbnail, prompt, kind — with one button, **Keep**. Seconds per asset, done while the
game is already on screen. Owners never promote: the catalogue is what every game draws from, so
its bar is ours.

Promotion requires the render's safety verdict to be a pass; a verdict-less render cannot be
promoted (same fail-closed rule as the save op).

The matcher's NONE log — what was asked for that nothing fit — is the tab's **gaps** list. It
says what to go and make next.

## Retirement

**Retire** is a soft state: the entry stops matching, the file stays for the games that used it.
Every vision call that shortlisted an entry and chose another or NONE counts a miss; entries with
many shortlists and no hits float to the top of the tab as candidates. A human retires.

## Matching

1. `generate_media` answers the path at once, exactly as it does now.
2. The control plane shortlists: same kind, `active`, token overlap between the request and each
   entry's prompt and tags, top twelve.
3. Empty shortlist → enqueue generation as today.
4. Otherwise one llm job with vision: the twelve thumbnails as numbered image parts, the request
   text, and the question *"Is one of these THE thing described, once restyled — colour and
   material may change, shape, parts and kind may not? Give one sentence of reasoning, then a
   final line `ANSWER: <number or NONE>`. When unsure, NONE."* The sentence is load-bearing:
   asked for the number alone the model answers NONE to a cottage with six cottages on screen
   (9/24 recall in the trial); allowed one sentence first it recalls 23/23 with the same zero
   false positives. Parse the last line only.
5. A number → copy the (styled) file to the path, record the entry id in the manifest. NONE →
   enqueue generation.

Worldgen's `RegionalObject` goes through the same call with `kind=mesh` and `category +
appearance` as the request.

The cost of a miss is one vision call before generation begins. Bounded, and measured below.

## Style — a restyle is a retexture

A kept mesh is geometry worth keeping; its texture is what the request changes. A restyle keeps
the catalogue mesh and regenerates its texture with the same reconstruction model that made it:
TRELLIS-2's texturing pipeline encodes the mesh's shape latent and samples a new texture latent
conditioned on an image, so the shape stage — the expensive and least reliable one — is skipped.

The conditioning image is the kept asset's own subject drawing, edited. Every entry keeps the
drawing it was reconstructed from beside the GLB; a restyle runs Qwen-Edit on that drawing with
the request as the instruction and "change nothing else", and the texturing pipeline takes the
edited drawing and the kept mesh. The drawing, not a render of the mesh, is what conditions:
measured 2026-08-22 on the church, a pipeline drawing gave a red tile roof on grey stone, lit and
readable, in 5 s at 2.9 GB; a Qwen-Edit of a *render* of the same mesh — correct to the eye, roof
and nothing else changed — came back as a dark, fully metallic object, because a flat-lit render
of a reconstruction reads to the image encoder as dark metal. The drawing is part of the entry
for that reason.

The edit prompt names a material, not a colour: "bright red clay tiles" returned a flat
saturated grid; "old weathered terracotta tiles, moss and grime where they were, same shading"
returned the original's weathering in a new material. The restyle prompt is the request's words
plus that shape.

What a retexture can and cannot do follows from what it is: colour, material and surface detail
change; parts do not. A red roof is a retexture; a door where the geometry has a wall is not, and
the vision step's rule — shape, parts and kind may not change — is what routes that request to
generation instead.

For 2D kinds the same shape is the Qwen-Edit pass alone on the kept render. Unmeasured.

A restyle is a new render and takes the same safety verdict as any other before it is saved.

## Measurement

1. **The trial — DONE 2026-08-22.** Seventeen building meshes (nine reused, eight drawn and
   reconstructed through the pipeline), 32 requests × 3 repeats, the local 27B with vision. It
   does not pick "closest": with one sentence of reasoning it recalled 67/72 hits and held
   17/24 NONEs at 17 entries, stable 30/32 across repeats, ~1.3 s per call; every request that
   was NONE before its asset existed became a pick after. The false positives were same-shape
   confusions (castle gatehouse → stone arch bridge, dovecote → watchtower), which is what the
   contact sheet in measurement 3 is for. The NONE rate fell as the catalogue grew (39/39 at
   four kinds → 17/24 at twelve) and is the number to watch.
2. **Hit rate against catalogue size**, replayed offline over a battery's prompts as the catalogue
   grows one game at a time.
3. **False positives**, as a contact sheet of every hit for a human to scan.
4. **The restyle chain end to end** — kept drawing → Qwen-Edit → texturing on the kept mesh. The
   two legs were measured separately on 2026-08-22; the edit-of-a-drawing feeding the texturing
   pipeline is the one unrun link.
5. **One game both ways** — wall-clock, GPU seconds, and whether a retextured catalogue mesh
   beside a generated one reads as one game.
