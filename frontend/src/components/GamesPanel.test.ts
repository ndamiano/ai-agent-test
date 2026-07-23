import { describe, it, expect } from 'vitest'
import { formatElapsed, stageFor, budgetFraction } from './GamesPanel'

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

describe('budgetFraction', () => {
    it('falls back to remaining/granted when no percentage is given', () => {
        expect(budgetFraction(null, 14400, 14400)).toBe(1)
        expect(budgetFraction(undefined, 7200, 14400)).toBe(0.5)
        expect(budgetFraction(null, 0, 14400)).toBe(0)
    })

    it('prefers the backend percentage, accepting either 0..1 or 0..100', () => {
        expect(budgetFraction(0.25, 999, 14400)).toBe(0.25)
        expect(budgetFraction(40, 999, 14400)).toBe(0.4)
    })

    it('clamps overdraw and an uncharged game to zero', () => {
        expect(budgetFraction(null, -100, 14400)).toBe(0)
        expect(budgetFraction(null, 0, 0)).toBe(0)
        expect(budgetFraction(150, 0, 14400)).toBe(1)
    })
})
