"""Styled prompt stage — turn each asset stub into a good, style-consistent image prompt and SAVE
it on the manifest.

Runs ONCE when a game's content is done, before generation (`run.run_build` sequences it between
loop-completion and `generate_images`). It reads the game's identity ONCE — the spec concept + the
story spine's tone/theme — into a single deterministic STYLE BRIEF, then composes the final image
prompt per stub by mixing that brief with the stub's subject through a per-kind `.txt` template.
The prompt is written back onto the stub (`entry["prompt"]`) so it is inspectable in the browser,
editable, climbable as a template, and re-runnable: `generate_images` and the per-asset regen both
consume ONLY the saved prompt, never re-deriving a subject at generation time.

Deterministic by construction — no LLM call. The style brief is data (concept + tone/theme), the
per-kind prompt is a template fill; the model-specific scaffolding (quality tags, framing, negative
prompts) stays in the comfyui `build_*_job` builders, so a `prompt` here is pure subject + look.
"""

from pathlib import Path
from typing import Dict

from renpy.templating import render_template
from maestro.asset_stubs import reconcile_stubs

_PROMPTS_DIR = Path(__file__).parent / "prompts"


def _spine(artifact: Dict) -> Dict:
    return (artifact.get("story", {}) or {}).get("spine", {}) or {}


def style_brief(artifact: Dict, spec: Dict) -> str:
    """The one art-direction line every asset shares — composed from the game's identity (concept +
    story tone/theme + presentation) so a game's assets read as one look. Rendered from a template
    so the phrasing is climbable."""
    spine = _spine(artifact)
    return render_template(_PROMPTS_DIR / "asset_style_brief.txt", {
        "concept": (spec.get("concept") or spec.get("title") or "").strip(),
        "theme": (spine.get("theme") or "").strip(),
        "tone": (spine.get("tone") or "").strip(),
        "presentation": spec.get("presentation", "2d"),
    }).strip()


def _styled(kind_template: str, subject: str, style: str) -> str:
    return render_template(_PROMPTS_DIR / kind_template,
                           {"subject": subject.strip(), "style": style}).strip()


# Per stub kind: the template + the field its subject comes from. Tiles keep their structured
# ideogram caption in build_tile_job (the model is trained on it), so their saved prompt is a
# human-readable descriptor for the browser, not what the tile builder consumes.
_KIND_TEMPLATE = {
    "backgrounds": "asset_background.txt",
    "characters":  "asset_character.txt",
    "tokens":      "asset_token.txt",
    "items":       "asset_item.txt",
    "cgs":         "asset_background.txt",
    "features":    "asset_feature.txt",
    "tiles":       "asset_tile.txt",
    "markers":     "asset_item.txt",
}


def apply_styled_prompts(artifact: Dict, spec: Dict) -> Dict:
    """Reconcile the manifest to completeness, then write a styled `prompt` onto every stub (and the
    shared `style` brief onto the manifest). Returns the styled manifest for the caller to persist."""
    manifest = reconcile_stubs(artifact, spec)
    brief = style_brief(artifact, spec)
    manifest["style"] = {"prompt": brief}

    for key, template in _KIND_TEMPLATE.items():
        for e in manifest.get(key) or []:
            subject = e.get("description") or e.get("label") or e.get("theme") or e.get("id") or ""
            e["prompt"] = _styled(template, subject, brief)

    tc = manifest.get("title_card")
    if isinstance(tc, dict):
        tc["prompt"] = _styled("asset_title_card.txt", tc.get("description") or "", brief)
    return manifest
