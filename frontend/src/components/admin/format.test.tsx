import { describe, it, expect } from 'vitest'
import { fmtSecs } from './format'

describe('fmtSecs', () => {
    it('renders raw seconds below an hour', () => {
        expect(fmtSecs(0)).toBe('0s')
        expect(fmtSecs(42.4)).toBe('42s')
        expect(fmtSecs(3599)).toBe('3599s')
    })

    it('compacts to one-decimal hours from an hour up', () => {
        expect(fmtSecs(3600)).toBe('1.0h')
        expect(fmtSecs(5400)).toBe('1.5h')
        expect(fmtSecs(12430)).toBe('3.5h')
    })
})
