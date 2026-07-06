// Types for the asset_manifest component-browser renderer (AssetManifestCard) — the gallery view
// over the run's actual generated image/mesh files, kept out of types/index.ts (a concurrent change
// is landing there).
import type { Asset } from './index'

export interface AssetManifestEntry {
    id: string
    image_file?: string
    description?: string
    name?: string
}

export interface AssetManifestTitleCard {
    image_file?: string
    description?: string
}

export interface AssetManifestContent {
    backgrounds?: AssetManifestEntry[]
    characters?: AssetManifestEntry[]
    items?: AssetManifestEntry[]
    cgs?: AssetManifestEntry[]
    title_card?: AssetManifestTitleCard
}

// The backend only injects `run_id` onto asset_manifest rows (games.py's list/get handlers) — it's
// the one component whose renderer needs to build asset-file URLs and call the per-asset
// regenerate endpoint, and the component-blind AssetRenderer signature (asset/editable/onSave)
// carries no run id otherwise.
export interface AssetManifestAsset extends Asset {
    run_id: string
    content: AssetManifestContent
}

// A distinct stamped world feature (hd2d walkable maps) — the source sprite + its optional 3D
// mesh, derived client-side from the places component's authored `layout.features`.
export interface WorldFeature {
    id: string
    kind?: string
    label: string
}
