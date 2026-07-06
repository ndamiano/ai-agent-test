import { describe, it, expect, afterEach } from 'vitest'
import type { Asset } from '../../types'
import SchemaCard from './SchemaCard'
import SceneCard from './renderers/SceneCard'
import { COMPONENT_VIEWS, getRenderer } from './registry'
import type { AssetRenderer } from './types'

describe('getRenderer (D3 registry + generic fallback)', () => {
    afterEach(() => {
        // Isolate tests from each other — a fake registration in one test must not leak into the next.
        COMPONENT_VIEWS.nodes = SceneCard
    })

    it('falls back to SchemaCard for a component with no registered renderer', () => {
        expect(getRenderer('some_brand_new_module_component')).toBe(SchemaCard)
    })

    it('has bespoke renderers for nodes/characters/places/combat/asset_manifest; everything else still falls back', () => {
        for (const component of ['items', 'story']) {
            expect(getRenderer(component)).toBe(SchemaCard)
        }
        for (const component of ['nodes', 'characters', 'places', 'combat', 'asset_manifest']) {
            expect(getRenderer(component)).not.toBe(SchemaCard)
        }
    })

    it('a bespoke renderer plugs in with exactly one registry line and wins over the fallback', () => {
        const FakeSceneCard: AssetRenderer = () => null
        // This is the exact one-line shape Wave 5 (D4) uses: COMPONENT_VIEWS.<component> = <Card>.
        COMPONENT_VIEWS.nodes = FakeSceneCard

        expect(getRenderer('nodes')).toBe(FakeSceneCard)
        // Unrelated components are unaffected — the registry is per-component, not global.
        expect(getRenderer('characters')).not.toBe(SchemaCard)
    })
})

describe('SchemaCard', () => {
    it('is a function component usable as the generic fallback for any asset shape', () => {
        const asset: Asset = { component: 'items', id: 'sword', idkey: 'items:sword', content: { name: 'Sword' }, dirty: false, review_note: '' }
        expect(typeof SchemaCard).toBe('function')
        // getRenderer must resolve to the same function reference SchemaCard exports.
        expect(getRenderer(asset.component)).toBe(SchemaCard)
    })
})
