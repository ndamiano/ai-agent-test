import { describe, expect, it } from 'vitest'
import { parseDesign } from './design'

const DESIGN = `Build this snail racing game over four in-game days. It is made of these systems. Every number below is a given value.

SYSTEMS: race loop, day cycle, snails, screens, audio, art.

RACE LOOP: four snails crawl a lane each; a race is a record with fields lane, distance, day.
DAY CYCLE: the sun crosses the track in 90 seconds.
SNAILS: a snail is a record with fields name, speed, stamina.
SCREENS: title (shows the four snails) → race → results.
AUDIO: generated with the Web Audio API: a start horn.
ART: ask for a snail as a sprite. Everything else — the track — is drawn in code.`

describe('parseDesign', () => {
    it('lifts the lead, the SYSTEMS list, and one section per NAME: paragraph', () => {
        const v = parseDesign(DESIGN)
        expect(v.lead).toMatch(/^Build this snail racing game/)
        expect(v.systems).toEqual(['race loop', 'day cycle', 'snails', 'screens', 'audio', 'art'])
        expect(v.sections.map(s => s.name)).toEqual(['RACE LOOP', 'DAY CYCLE', 'SNAILS', 'SCREENS', 'AUDIO', 'ART'])
        expect(v.sections[0].body).toMatch(/^four snails crawl a lane each/)
        expect(v.sections[5].body).toMatch(/drawn in code\.$/)
    })

    it('keeps a paragraph that wraps onto more lines inside its section', () => {
        const v = parseDesign('RACE LOOP: one line.\nand a second line.\n\nDAY CYCLE: sun.')
        expect(v.sections).toEqual([
            { name: 'RACE LOOP', body: 'one line.\nand a second line.' },
            { name: 'DAY CYCLE', body: 'sun.' },
        ])
    })

    it('shows text with no shape — the words themselves — as one unnamed section', () => {
        const v = parseDesign('a snail racing game\nover four days')
        expect(v.lead).toBe('')
        expect(v.systems).toEqual([])
        expect(v.sections).toEqual([{ name: null, body: 'a snail racing game\nover four days' }])
    })

    it('is empty for empty text', () => {
        expect(parseDesign('')).toEqual({ lead: '', systems: [], sections: [] })
    })
})
