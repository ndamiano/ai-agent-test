import { describe, it, expect } from 'vitest'
import { toolLabel, updateToolEvents } from './ChatPanel'

describe('toolLabel', () => {
    it('maps known chat tools to friendly progress copy', () => {
        expect(toolLabel('propose_game_spec')).toBe('Drafting the spec')
        expect(toolLabel('amend_game_spec')).toBe('Amending the spec')
    })

    it('falls back to a generic label for an unmapped tool', () => {
        expect(toolLabel('some_new_tool')).toBe('Running some_new_tool')
    })
})

describe('updateToolEvents', () => {
    it('appends a new in-flight entry on start', () => {
        const next = updateToolEvents([], { tool_name: 'propose_game_spec', status: 'start' })
        expect(next).toEqual([{ tool_name: 'propose_game_spec', status: 'start' }])
    })

    it('resolves the matching in-flight entry in place on success/failed', () => {
        const started = [{ tool_name: 'propose_game_spec', status: 'start' as const }]
        const resolved = updateToolEvents(started, { tool_name: 'propose_game_spec', status: 'success' })
        expect(resolved).toEqual([{ tool_name: 'propose_game_spec', status: 'success' }])
    })

    it('tracks multiple distinct tools independently', () => {
        let state: { tool_name: string; status: 'start' | 'success' | 'failed' }[] = []
        state = updateToolEvents(state, { tool_name: 'propose_game_spec', status: 'start' })
        state = updateToolEvents(state, { tool_name: 'propose_game_spec', status: 'success' })
        state = updateToolEvents(state, { tool_name: 'web_search', status: 'start' })
        state = updateToolEvents(state, { tool_name: 'web_search', status: 'failed' })
        expect(state).toEqual([
            { tool_name: 'propose_game_spec', status: 'success' },
            { tool_name: 'web_search', status: 'failed' },
        ])
    })
})
