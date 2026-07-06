import type { AssetRenderer } from './types'
import SchemaCard from './SchemaCard'
import SceneCard from './renderers/SceneCard'
import CharacterCard from './renderers/CharacterCard'
import PlaceCard from './renderers/PlaceCard'
import EncounterCard from './renderers/EncounterCard'
import AssetManifestCard from './renderers/AssetManifestCard'

// The module-owns-its-presentation registry (mirrors the backend's per-module prompt blocks).
// D4/Wave 5 plugs a bespoke renderer in with ONE line each, no shell change.
// Until an entry exists for a component id, getRenderer falls back to the generic SchemaCard —
// so a brand-new module is fully usable (view/edit/thumb) the day it lands.
export const COMPONENT_VIEWS: Record<string, AssetRenderer> = {
    nodes: SceneCard,
    characters: CharacterCard,
    places: PlaceCard,
    combat: EncounterCard,
    asset_manifest: AssetManifestCard,
}

export function getRenderer(component: string): AssetRenderer {
    return COMPONENT_VIEWS[component] ?? SchemaCard
}
