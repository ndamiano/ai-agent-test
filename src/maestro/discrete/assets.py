"""assets — the image manifest. Owns `asset_manifest`.

Contributes the shared floor (a game has backgrounds). The spine modules add what *they* depend
on: dialogue requires character sprites have ids; navigation requires backgrounds have ids.
"""

from maestro.modules import Module
from maestro.discrete.validators import v_asset_manifest, SKEL_ASSET_MANIFEST


def _asset_manifest_view(c):
    """Upstream view for downstream authors: just the ids. A node author needs the background ids
    (to set a scene's `location`) and the character ids — never the image-generation prose, which
    is the single biggest chunk of dead weight in every node step's context."""
    def ids(key):
        return [x["id"] for x in (c.get(key) or []) if isinstance(x, dict) and x.get("id")]
    view = {"backgrounds": ids("backgrounds"), "characters": ids("characters")}
    if c.get("items"):
        view["items"] = ids("items")
    return view


MODULE = Module(
    id="assets",
    components=("asset_manifest",),
    schemas={"asset_manifest": v_asset_manifest},
    skeletons={"asset_manifest": SKEL_ASSET_MANIFEST},
    baseline={"asset_manifest": [
        {"type": "exists", "path": "asset_manifest.backgrounds"},
    ]},
    mode_tools=frozenset({"write_component", "update_scratchpad", "request_review"}),
    mode_prompt="mode_asset.txt",
    context_view=_asset_manifest_view,
)
