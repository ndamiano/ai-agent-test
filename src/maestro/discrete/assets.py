"""assets — the image manifest. Owns `asset_manifest`.

Contributes the shared floor (a game has backgrounds). The spine modules add what *they* depend
on: dialogue requires character sprites have ids; navigation requires backgrounds have ids.
"""

from maestro.modules import Module
from maestro.discrete.validators import v_asset_manifest, SKEL_ASSET_MANIFEST

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
)
