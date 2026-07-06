import { describe, it, expect } from 'vitest'
import { splitSseFrames } from './client'

describe('splitSseFrames', () => {
    it('extracts complete frames and keeps a trailing partial frame as rest', () => {
        const buffer = 'data: {"type":"token","content":"Hi"}\n\ndata: {"type":"done","message":"Hi"}\n\ndata: {"type":"tok'
        const { frames, rest } = splitSseFrames(buffer)
        expect(frames).toEqual([
            'data: {"type":"token","content":"Hi"}',
            'data: {"type":"done","message":"Hi"}',
        ])
        expect(rest).toBe('data: {"type":"tok')
    })

    it('returns no frames and the whole buffer as rest when nothing is complete yet', () => {
        const { frames, rest } = splitSseFrames('data: {"type":"tok')
        expect(frames).toEqual([])
        expect(rest).toBe('data: {"type":"tok')
    })

    it('handles an empty buffer', () => {
        expect(splitSseFrames('')).toEqual({ frames: [], rest: '' })
    })
})
