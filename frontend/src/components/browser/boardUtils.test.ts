import { describe, it, expect } from 'vitest'
import type { Asset } from '../../types'
import { patchAsset, markDirtyByIdkeys, computeDirtyCounts, splitIdkey } from './boardUtils'

function asset(component: string, id: string, dirty = false): Asset {
    return { component, id, idkey: `${component}:${id}`, content: {}, dirty, review_note: '' }
}

describe('splitIdkey', () => {
    it('splits component:item', () => {
        expect(splitIdkey('nodes:scene_3')).toEqual(['nodes', 'scene_3'])
    })
    it('falls back to the whole string on both sides when there is no colon', () => {
        expect(splitIdkey('bare')).toEqual(['bare', 'bare'])
    })
})

describe('patchAsset', () => {
    it('updates the matching item in place and leaves other components untouched', () => {
        const board = { nodes: [asset('nodes', 'a'), asset('nodes', 'b')], characters: [asset('characters', 'mara')] }
        const next = patchAsset(board, 'nodes', 'b', { dirty: true, review_note: 'too flat' })
        expect(next.nodes[0].dirty).toBe(false)
        expect(next.nodes[1]).toMatchObject({ dirty: true, review_note: 'too flat' })
        expect(next.characters).toBe(board.characters) // untouched component: same reference
    })

    it('is a no-op when the component or item is not loaded yet', () => {
        const board = { nodes: [asset('nodes', 'a')] }
        expect(patchAsset(board, 'places', 'x', { dirty: true })).toBe(board)
        expect(patchAsset(board, 'nodes', 'missing', { dirty: true })).toBe(board)
    })
})

describe('markDirtyByIdkeys', () => {
    it('flags every referenced idkey across components (edit-propagation, D5)', () => {
        const board = {
            nodes: [asset('nodes', 'scene_4'), asset('nodes', 'scene_12')],
            places: [asset('places', 'town')],
        }
        const next = markDirtyByIdkeys(board, ['nodes:scene_12', 'places:town', 'unknown:x'])
        expect(next.nodes[0].dirty).toBe(false)
        expect(next.nodes[1].dirty).toBe(true)
        expect(next.places[0].dirty).toBe(true)
    })
})

describe('computeDirtyCounts', () => {
    it('derives a per-component dirty count for the tab badges (D7)', () => {
        const board = {
            nodes: [asset('nodes', 'a', true), asset('nodes', 'b', false), asset('nodes', 'c', true)],
            characters: [asset('characters', 'mara', false)],
        }
        expect(computeDirtyCounts(board, ['nodes', 'characters', 'places'])).toEqual({
            nodes: 2, characters: 0, places: 0,
        })
    })

    it('gives every requested component an entry even before its assets have loaded', () => {
        expect(computeDirtyCounts({}, ['nodes', 'combat'])).toEqual({ nodes: 0, combat: 0 })
    })
})
