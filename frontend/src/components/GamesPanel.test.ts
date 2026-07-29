import { describe, it, expect } from 'vitest'
import { formatElapsed, stageFor, budgetFraction, shouldAdoptPrompt } from './GamesPanel'

describe('shouldAdoptPrompt', () => {
    it('fills the box on the first load', () => {
        expect(shouldAdoptPrompt(null, 'an open world RPG')).toBe(true)
    })

    it('leaves an in-progress edit alone when the poll returns the same text', () => {
        expect(shouldAdoptPrompt('an open world RPG', 'an open world RPG')).toBe(false)
    })

    it('adopts text the server changed under us', () => {
        expect(shouldAdoptPrompt('an open world RPG', 'an open world RPG with card combat')).toBe(true)
    })
})

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
    it('is building whenever a build is running, built or not', () => {
        expect(stageFor(true, false)).toBe('building')
        expect(stageFor(true, true)).toBe('building')
    })

    it('is built once a build has produced a bundle and nothing is running', () => {
        expect(stageFor(false, true)).toBe('built')
    })

    it('is ready when nothing has been built yet', () => {
        expect(stageFor(false, false)).toBe('ready')
    })
})

describe('budgetFraction', () => {
    it('is null (no bar) when the game is uncharged', () => {
        expect(budgetFraction(null)).toBeNull()
        expect(budgetFraction(undefined)).toBeNull()
    })

    it('passes a 0..1 fraction through, and normalizes a 0..100 percentage', () => {
        expect(budgetFraction(0.25)).toBe(0.25)
        expect(budgetFraction(1)).toBe(1)
        expect(budgetFraction(40)).toBe(0.4)
    })

    it('clamps to [0,1]', () => {
        expect(budgetFraction(-0.5)).toBe(0)
        expect(budgetFraction(150)).toBe(1)
    })
})
