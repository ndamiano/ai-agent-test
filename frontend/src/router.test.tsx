import { describe, expect, it } from 'vitest'
import { parseRoute } from './router'

describe('parseRoute', () => {
    it('maps each path to its view', () => {
        expect(parseRoute('/')).toEqual({ kind: 'library' })
        expect(parseRoute('/new')).toEqual({ kind: 'create' })
        expect(parseRoute('/settings')).toEqual({ kind: 'settings' })
        expect(parseRoute('/credits')).toEqual({ kind: 'credits' })
        expect(parseRoute('/admin')).toEqual({ kind: 'admin', tab: 'queues' })
        expect(parseRoute('/admin/costs')).toEqual({ kind: 'admin', tab: 'costs' })
        expect(parseRoute('/game/abc123')).toEqual({ kind: 'game', runId: 'abc123' })
    })

    it('lands unknown or malformed paths on the library', () => {
        expect(parseRoute('/nope')).toEqual({ kind: 'library' })
        expect(parseRoute('/admin/nope')).toEqual({ kind: 'library' })
        expect(parseRoute('/game/')).toEqual({ kind: 'library' })
        expect(parseRoute('/game/abc/extra')).toEqual({ kind: 'library' })
        expect(parseRoute('/game/../etc')).toEqual({ kind: 'library' })
    })
})
