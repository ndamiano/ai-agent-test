import { describe, expect, it } from 'vitest'
import { act, renderHook } from '@testing-library/react'
import type { ConsoleReport } from './useConsoleReports'
import { MAX_REPORTS, foldReport, reportsAsNote, useConsoleReports } from './useConsoleReports'

const msg = (over: Record<string, unknown> = {}) =>
    ({ source: 'maestro-report', kind: 'error', message: 'boom', frame: 'at tick (main.js:12)', ...over })

describe('foldReport', () => {
    it('ignores anything that is not a reporter message', () => {
        expect(foldReport([], { source: 'other', message: 'x' })).toEqual([])
        expect(foldReport([], {})).toEqual([])
        expect(foldReport([], { source: 'maestro-report' })).toEqual([])  // no message
    })

    it('counts repeats instead of listing them — a rAF throw arrives 60/s', () => {
        let list: ConsoleReport[] = []
        for (let i = 0; i < 180; i++) list = foldReport(list, msg())
        expect(list).toHaveLength(1)
        expect(list[0].count).toBe(180)
    })

    it('keeps distinct errors distinct', () => {
        let list = foldReport([], msg())
        list = foldReport(list, msg({ message: 'other boom' }))
        list = foldReport(list, msg({ kind: 'console-warn' }))
        expect(list).toHaveLength(3)
    })

    it('caps how many distinct errors it will hold', () => {
        let list: ConsoleReport[] = []
        for (let i = 0; i < MAX_REPORTS + 10; i++) list = foldReport(list, msg({ message: `e${i}` }))
        expect(list).toHaveLength(MAX_REPORTS)
    })

    it('falls back to src:line when there is no stack frame', () => {
        const list = foldReport([], msg({ frame: null, src: 'main.js', line: 40 }))
        expect(list[0].detail).toBe('main.js:40')
    })
})

describe('useConsoleReports', () => {
    const send = (origin: string, data: unknown) =>
        act(() => { window.dispatchEvent(new MessageEvent('message', { origin, data })) })

    it('accepts messages only from the game origin — anyone can postMessage a window', () => {
        const { result } = renderHook(() => useConsoleReports('https://games.example'))
        send('https://evil.example', msg())
        expect(result.current.reports).toHaveLength(0)
        send('https://games.example', msg())
        expect(result.current.reports).toHaveLength(1)
    })

    it('listens to nothing when there is no live session', () => {
        const { result } = renderHook(() => useConsoleReports(null))
        send('https://games.example', msg())
        expect(result.current.reports).toHaveLength(0)
    })
})

describe('reportsAsNote', () => {
    it('writes one line per deduped error, with counts and locations', () => {
        let list = foldReport([], msg())
        list = foldReport(list, msg())
        list = foldReport(list, msg({ kind: 'rejection', message: 'undefined is not a function', frame: null }))
        expect(reportsAsNote(list)).toBe(
            '- [error] boom (×2) — at tick (main.js:12)\n' +
            '- [rejection] undefined is not a function',
        )
    })
})
