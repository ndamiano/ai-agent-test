import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, render, screen } from '@testing-library/react'
import { WorkingPanel } from './WorkingPanel'
import type { GameAsset } from '../../types'

afterEach(() => { cleanup(); vi.restoreAllMocks() })

const asset = (over: Partial<GameAsset> = {}): GameAsset =>
    ({ id: 'goblin', kind: 'sprite', status: 'ready', prompt: 'a goblin', defect: null, ...over })

const panel = (over: Partial<React.ComponentProps<typeof WorkingPanel>> = {}) => render(
    <WorkingPanel runId="r1" paused={false} step={47} summary="wrote race_day.js" elapsedSec={372}
        assets={null} assetsVersion={0} budget={0.78} prompt="a snail racing game" feed={[]} {...over} />,
)

describe('WorkingPanel', () => {
    it('leads with what the build is doing right now', () => {
        panel()
        expect(screen.getByText('wrote race_day.js')).toBeTruthy()
        expect(screen.getByText('47')).toBeTruthy()
        expect(screen.getByText('6:12')).toBeTruthy()
    })

    it('says so before the first step lands', () => {
        panel({ step: null, summary: null })
        expect(screen.getByText(/starting the build/i)).toBeTruthy()
        expect(screen.getByText('—')).toBeTruthy()
    })

    it('says nothing is running when paused', () => {
        panel({ paused: true })
        expect(screen.getByText(/paused/i)).toBeTruthy()
    })

    it('counts the art that has landed against what was asked for', () => {
        panel({ assets: [asset(), asset({ id: 'hut', status: 'pending' })] })
        expect(screen.getByText('1 of 2')).toBeTruthy()
    })

    // A build ends when the model calls done, so there is no total to be a fraction of.
    it('shows no progress bar and no estimate', () => {
        const { container } = panel()
        expect(container.querySelector('[role="progressbar"]')).toBeNull()
        expect(screen.queryByText(/remaining|estimate|eta/i)).toBeNull()
    })
})
