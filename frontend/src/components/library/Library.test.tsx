import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { Library } from './Library'
import { api } from '../../api/client'
import type { Game } from '../../types'

const game = (over: Partial<Game> = {}): Game =>
    ({ run_id: 'r1', title: 'Island Village', built: true, building: false, paused: false, ...over })

afterEach(() => { cleanup(); vi.restoreAllMocks() })

const shelf = (games: Game[], onOpen = () => {}, onNew = () => {}) =>
    render(<Library games={games} loading={false} error={null} onOpen={onOpen} onNew={onNew} />)

describe('Library', () => {
    it('offers a way to make one even when there is nothing yet', () => {
        const onNew = vi.fn()
        shelf([], () => {}, onNew)

        fireEvent.click(screen.getByText('Make a new game'))

        expect(onNew).toHaveBeenCalled()
    })

    // Making a game is the point of the product, so it leads the shelf rather than trailing it.
    it('puts making a new one first, ahead of every game', () => {
        vi.spyOn(api, 'getGameAssets').mockResolvedValue([])
        shelf([game(), game({ run_id: 'r2', title: 'Forest Cafe' })])

        const cards = screen.getAllByRole('button')

        expect(cards[0].textContent).toContain('Make a new game')
    })

    it('covers a built game with its own first rendered image', async () => {
        vi.spyOn(api, 'getGameAssets').mockResolvedValue([
            { id: 'village', kind: 'image', status: 'ready', prompt: 'a village' },
        ])
        vi.spyOn(api, 'getAssetBlobUrl').mockResolvedValue('blob:village')

        shelf([game()])

        await waitFor(() => expect(document.querySelector('img')).toBeTruthy())
        expect(document.querySelector('img')?.getAttribute('src')).toBe('blob:village')
    })

    // Roughly half of all builds render no art. A card for one must still be a card.
    it('falls back to the sigil when the game rendered no art', async () => {
        vi.spyOn(api, 'getGameAssets').mockResolvedValue([])
        const blob = vi.spyOn(api, 'getAssetBlobUrl')

        shelf([game()])

        await waitFor(() => expect(document.querySelector('canvas')).toBeTruthy())
        expect(document.querySelector('img')).toBeNull()
        expect(blob).not.toHaveBeenCalled()
    })

    it('does not ask for a cover for a game that has never been built', () => {
        const assets = vi.spyOn(api, 'getGameAssets')

        shelf([game({ built: false })])

        expect(assets).not.toHaveBeenCalled()
        expect(screen.getByText('not built')).toBeTruthy()
    })

    it('says which games are working', () => {
        shelf([game({ built: false, building: true }), game({ run_id: 'r2', title: 'Forest Cafe' })])

        expect(screen.getByText('working')).toBeTruthy()
        expect(screen.getByText('2 made · 1 working')).toBeTruthy()
    })

    it('opens the game that was clicked', () => {
        const onOpen = vi.fn()
        vi.spyOn(api, 'getGameAssets').mockResolvedValue([])
        shelf([game()], onOpen)

        fireEvent.click(screen.getByText('Island Village'))

        expect(onOpen).toHaveBeenCalledWith('r1')
    })
})
