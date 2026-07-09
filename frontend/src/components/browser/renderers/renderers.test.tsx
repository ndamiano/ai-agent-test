import { describe, it, expect, vi } from 'vitest'
import { render, waitFor } from '@testing-library/react'
import type { Asset } from '../../../types'
import SceneCard from './SceneCard'
import CharacterCard from './CharacterCard'
import PlaceCard from './PlaceCard'
import EncounterCard from './EncounterCard'
import AssetManifestCard from './AssetManifestCard'
import { api } from '../../../api/client'

const asset = (component: string, id: string, content: Record<string, unknown>): Asset => ({
    component, id, idkey: `${component}:${id}`, content, dirty: false, review_note: '',
})

const onSave = vi.fn(async () => {})

describe('D4 bespoke component-browser renderers mount for a sample asset of their type', () => {
    it('SceneCard renders a screenplay node (lines + menu choices)', () => {
        const a = asset('nodes', 'scene_1', {
            location: 'bg_crypt',
            lines: [
                { speaker: 'mara', emotion: 'angry', text: "We shouldn't be here." },
                { speaker: null, text: 'The torch gutters.' },
            ],
            end: {
                type: 'menu',
                choices: [{ text: 'Press on', target: 'scene_2', requires: { flag: 'has_key' }, effects: [] }],
            },
        })
        const { container } = render(<SceneCard asset={a} editable onSave={onSave} />)
        expect(container.textContent).toContain('mara')
        expect(container.textContent).toContain("We shouldn't be here.")
        expect(container.textContent).toContain('NARR')
        expect(container.textContent).toContain('Press on')
        expect(container.textContent).toContain('menu')
    })

    it('CharacterCard renders name/bio/traits/expressions', () => {
        const a = asset('characters', 'mara', {
            id: 'mara', name: 'Mara', role: 'protagonist', voice: 'clipped, impatient',
            temperament: 'guarded', drive: 'wants out', sex: 'female',
            history: ['grew up in the yard'], competencies: ['lockpicking'],
            example_lines: ['Move.'], color: '#c8ffc8',
            expressions: { happy: 'mara_happy.png' },
        })
        const { container } = render(<CharacterCard asset={a} editable onSave={onSave} />)
        expect(container.textContent).toContain('Mara')
        expect(container.textContent).toContain('protagonist')
        expect(container.textContent).toContain('clipped, impatient')
        expect(container.textContent).toContain('lockpicking')
        expect(container.textContent).toContain('happy')
    })

    it('PlaceCard renders a walkable world_map tile grid', () => {
        const a = asset('places', 'zone_crypt', {
            kind: 'world_map',
            tiles: { rows: ['###', '#.#', '###'], legend: {} },
            interactables: [{ id: 'h_gate', label: 'Gate', position: { cell: { x: 1, y: 1 } }, action: { type: 'move' } }],
            layout: { features: [{ id: 'f_gate', kind: 'gate', label: 'Gate' }], exits: [{ id: 'x_south', edge: 'south' }] },
        })
        const { container } = render(<PlaceCard asset={a} editable onSave={onSave} />)
        expect(container.textContent).toContain('world_map')
        expect(container.textContent).toContain('Gate')
        expect(container.textContent).toContain('south')
    })

    it('PlaceCard renders a PnC room as a hotspot list', () => {
        const a = asset('places', 'room_1', {
            kind: 'room', background: 'bg_room1',
            interactables: [{ id: 'h_desk', label: 'desk', position: { rect: { x: 0, y: 0, w: 1, h: 1 } }, action: { type: 'examine' } }],
        })
        const { container } = render(<PlaceCard asset={a} editable onSave={onSave} />)
        expect(container.textContent).toContain('room')
        expect(container.textContent).toContain('desk')
        expect(container.textContent).toContain('examine')
    })

    it('EncounterCard renders each flattened combat item shape', () => {
        const cases: [string, Record<string, unknown>][] = [
            ['hp', { id: 'hp', default: 30, role: 'resource_depletable', min: 0, max: 30 }],
            ['slash', { id: 'slash', name: 'Slash', targeting: { shape: 'single', faction: 'enemy' }, effects: [{ stat: 'hp', op: 'damage', formula: { base: 4 } }] }],
            ['cb_hero', { id: 'cb_hero', character: 'mara', stats: [{ stat: 'hp', value: 30 }], abilities: ['slash'], xp_yield: 10 }],
            ['enc_crypt', { id: 'enc_crypt', combatants: [{ ref: 'cb_hero', faction: 'player' }], victory: { all_defeated: 'enemy' }, on_victory: { type: 'jump', target: 'scene_2' } }],
            ['poison', { id: 'poison', name: 'Poison', tick: [{ stat: 'hp', op: 'damage' }] }],
        ]
        for (const [id, content] of cases) {
            const { container, unmount } = render(<EncounterCard asset={asset('combat', id, content)} editable onSave={onSave} />)
            expect(container.textContent).not.toBe('')
            unmount()
        }
    })
})

describe('AssetManifestCard renders the declared-asset gallery against the run\'s served images', () => {
    it('shows each backgrounds/characters/cgs/title_card entry, loading images via the run-scoped authed fetch, plus a Regenerate control', async () => {
        vi.spyOn(api, 'listAssets').mockResolvedValue([])
        // Images now load over an authed fetch (token on the header, not the URL) into a blob: URL.
        const fetchAsset = vi.spyOn(api, 'fetchAssetObjectUrl')
            .mockImplementation(async (_runId, filename) => `blob:mock/${filename}`)
        const manifestAsset = {
            ...asset('asset_manifest', 'asset_manifest', {
                backgrounds: [{ id: 'bg_dock', image_file: 'bg_dock.png', description: 'a foggy dock' }],
                characters: [{ id: 'mara', image_file: 'mara.png', description: 'a navigator' }],
                items: [],
                cgs: [{ id: 'cg_win', image_file: 'cg_win.png', description: 'victory' }],
                title_card: { image_file: 'title_card.png', description: 'the title' },
            }),
            run_id: 'run_g1',
        }

        const { container } = render(<AssetManifestCard asset={manifestAsset as Asset} editable onSave={onSave} />)

        expect(container.textContent).toContain('bg_dock')
        expect(container.textContent).toContain('a foggy dock')
        expect(container.textContent).toContain('mara')
        expect(container.textContent).toContain('cg_win')
        expect(container.textContent).toContain('title_card')

        // The bytes are fetched run-scoped by (runId, filename) — no token-bearing URL anywhere.
        expect(fetchAsset).toHaveBeenCalledWith('run_g1', 'bg_dock.png')
        const img = await waitFor(() => {
            const el = container.querySelector('img[alt="bg_dock"]') as HTMLImageElement | null
            if (!el) throw new Error('image not yet loaded')
            return el
        })
        expect(img.src).toContain('blob:mock/bg_dock.png')

        // one Regenerate button per gallery tile (4 declared assets), editable=true
        const buttons = Array.from(container.querySelectorAll('button'))
            .filter(b => b.textContent?.includes('Regenerate'))
        expect(buttons.length).toBe(4)
    })

    it('surfaces a clear error instead of broken images when the row has no run id', () => {
        const manifestAsset = asset('asset_manifest', 'asset_manifest', { backgrounds: [] })
        const { container } = render(<AssetManifestCard asset={manifestAsset} editable onSave={onSave} />)
        expect(container.textContent).toContain('no run id')
    })
})
