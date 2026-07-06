import { describe, it, expect } from 'vitest'
import { formatElapsed, identityToIdkey } from './GamesPanel'

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

describe('identityToIdkey', () => {
    it('matches the backend idkey format — null in place of empty path/ref', () => {
        // Error.identity() serializes an absent path/ref as "" (module.py); idkey() serializes
        // the same absent fields as null — this converts one to the other.
        expect(identityToIdkey(['build', 'min_count', 'cast', '', '']))
            .toBe(JSON.stringify(['build', 'min_count', 'cast', null, null]))
    })

    it('preserves a real path/ref', () => {
        expect(identityToIdkey(['build', 'dangling_ref', 'nodes', 'scene_3', 'has_key']))
            .toBe(JSON.stringify(['build', 'dangling_ref', 'nodes', 'scene_3', 'has_key']))
    })
})
