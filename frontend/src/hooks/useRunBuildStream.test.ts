import { describe, it, expect } from 'vitest'
import { mergeEvents, foldStream } from './useRunBuildStream'
import type { DurableEventRow, WebSocketMessage } from '../types'

const row = (id: number, kind: string, created_at: number, payload: Record<string, any> = {}): DurableEventRow =>
    ({ id, game_id: 'g', build_id: null, kind, created_at, payload })

// ts is epoch-SECONDS (matching row's created_at); the timestamp field is the ISO string the
// backend actually sends, so parseTs round-trips it back to ms.
const live = (type: string, ts: number, extra: Record<string, any> = {}): WebSocketMessage =>
    ({ type, run_id: 'g', timestamp: new Date(ts * 1000).toISOString(), ...extra })

describe('mergeEvents', () => {
    it('replay owns the prefix before the live window, live owns the rest — no double count', () => {
        const rows = [row(1, 'build_started', 100), row(2, 'build_step', 150), row(3, 'build_step', 200)]
        // socket connected at t=180: it re-delivers the 200s step live. Merge must not duplicate it.
        const msgs = [live('build_step', 200, { step: 2 }), live('build_done', 220, { ok: true })]
        const merged = mergeEvents(rows, msgs)
        // rows at 100 and 150 kept (before firstLiveTs=200); the 200 row dropped in favor of live.
        expect(merged.map(e => e.at)).toEqual([100_000, 150_000, 200_000, 220_000])
        expect(merged.filter(e => e.at === 200_000)).toHaveLength(1)
        expect(merged.find(e => e.at === 200_000)!.key).toBe('l0')  // the live copy won
    })

    it('with no live events, replay carries the whole feed (reload of a finished build)', () => {
        const rows = [row(1, 'build_started', 100), row(2, 'build_done', 300, { ok: true })]
        const merged = mergeEvents(rows, [])
        expect(merged.map(e => e.type)).toEqual(['build_started', 'build_done'])
    })

    it('sorts the merged stream by time', () => {
        const merged = mergeEvents([row(1, 'build_started', 30)], [live('build_done', 40, { ok: false })])
        expect(merged.map(e => e.at)).toEqual([30_000, 40_000])
    })
})

describe('foldStream', () => {
    it('derives progress and start time from the stream', () => {
        const s = foldStream(mergeEvents([
            row(1, 'build_started', 100, { started_at: 100 }),
            row(2, 'build_step', 130, { step: 4, summary: 'wrote index.html', elapsed: 30 }),
        ], []))
        expect(s.progress).toEqual({ step: 4, summary: 'wrote index.html' })
        expect(s.startedAt).toBeCloseTo(100)  // 130 - elapsed 30
        expect(s.feed.map(f => f.text)).toEqual([
            'build started',
            'step 4: wrote index.html',
        ])
    })

    it('shows why a build failed, not just that it did', () => {
        const s = foldStream(mergeEvents([
            row(1, 'build_done', 100, { ok: false, error: 'compute exhausted: budget refused' }),
        ], []))
        expect(s.feed.map(f => f.text)).toEqual(['✗ build failed — compute exhausted: budget refused'])
    })

    it('falls back to a bare failure line when build_done carries no error', () => {
        const s = foldStream(mergeEvents([row(1, 'build_done', 100, { ok: false })], []))
        expect(s.feed.map(f => f.text)).toEqual(['✗ build failed'])
    })

    it('tracks the skinning flag across asset start/done', () => {
        expect(foldStream(mergeEvents([row(1, 'assets_started', 100)], [])).skinning).toBe(true)
        expect(foldStream(mergeEvents([
            row(1, 'assets_started', 100),
            row(2, 'assets_done', 200, { ok: true, rendered: 5 }),
        ], [])).skinning).toBe(false)
    })

    it('ignores state-only event kinds (no feed line)', () => {
        const s = foldStream(mergeEvents([row(1, 'prompt_proposed', 100), row(2, 'job_done', 110)], []))
        expect(s.feed).toHaveLength(0)
    })
})
