import { describe, expect, it } from 'vitest'
import { hashSeed } from './Sigil'

// A game with no rendered art wears a mark derived from its run id, so the mark has to be the same
// every time that game is drawn — on any machine, in any session.
describe('hashSeed', () => {
    it('is stable for the same run id', () => {
        expect(hashSeed('0290f8d1db34')).toBe(hashSeed('0290f8d1db34'))
    })

    it('separates run ids that differ by one character', () => {
        expect(hashSeed('0290f8d1db34')).not.toBe(hashSeed('0290f8d1db35'))
    })

    it('stays a 32-bit unsigned integer', () => {
        for (const seed of ['', 'a', 'b10f5cae0ebd', 'x'.repeat(400)]) {
            const h = hashSeed(seed)
            expect(Number.isInteger(h)).toBe(true)
            expect(h).toBeGreaterThanOrEqual(0)
            expect(h).toBeLessThan(2 ** 32)
        }
    })
})
