import { describe, it, expect } from 'vitest'
import { formatElapsed, stageFor, computeRemaining } from './GamesPanel'

describe('formatElapsed', () => {
    it('renders m:ss, zero-padding seconds', () => {
        expect(formatElapsed(0)).toBe('0:00')
        expect(formatElapsed(5)).toBe('0:05')
        expect(formatElapsed(65)).toBe('1:05')
        expect(formatElapsed(3661)).toBe('61:01')
    })

    it('floors fractional seconds and clamps negative input to zero', () => {
        expect(formatElapsed(12.9)).toBe('0:12')
        expect(formatElapsed(-3)).toBe('0:00')
    })
})

describe('stageFor', () => {
    it('is draft until frozen, regardless of build state', () => {
        expect(stageFor(false, false, false)).toBe('draft')
        expect(stageFor(false, true, true)).toBe('draft')
    })

    it('is building while a frozen spec is mid-build', () => {
        expect(stageFor(true, true, false)).toBe('building')
        expect(stageFor(true, true, true)).toBe('building')
    })

    it('is built once a build has produced a bundle and nothing is running', () => {
        expect(stageFor(true, false, true)).toBe('built')
    })

    it('is ready when frozen but never built', () => {
        expect(stageFor(true, false, false)).toBe('ready')
    })
})

describe('computeRemaining', () => {
    it('is the remaining fraction of the granted budget', () => {
        expect(computeRemaining(14400, 0)).toBe(1)
        expect(computeRemaining(14400, 7200)).toBe(0.5)
        expect(computeRemaining(14400, 14400)).toBe(0)
    })

    it('clamps overdraw to zero and an uncharged game to zero', () => {
        expect(computeRemaining(14400, 20000)).toBe(0)
        expect(computeRemaining(0, 0)).toBe(0)
    })
})
