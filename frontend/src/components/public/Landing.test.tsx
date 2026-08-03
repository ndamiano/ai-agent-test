import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, render, screen, waitFor } from '@testing-library/react'
import Landing from './Landing'
import { RouterProvider } from '../../router'
import { api } from '../../api/client'

const renderLanding = () =>
    render(
        <RouterProvider>
            <Landing />
        </RouterProvider>,
    )

afterEach(() => {
    cleanup()
    vi.restoreAllMocks()
})

describe('landing demo tiers', () => {
    it('groups demos under their tier headings, showcase first', async () => {
        vi.spyOn(api, 'listDemos').mockResolvedValue([
            { run_id: 'aaa', title: 'Duel', prompt: 'a duel game', tier: 'showcase' },
            { run_id: 'bbb', title: 'Fox', prompt: 'a fox game', tier: 'oneshot' },
        ])
        renderLanding()
        await waitFor(() => screen.getByText('a fox game'))

        const showcase = screen.getByText("What's possible")
        const oneshot = screen.getByText('What one sentence gets you')
        expect(showcase.compareDocumentPosition(oneshot) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
        expect(oneshot.compareDocumentPosition(screen.getByText('a fox game')) &
            Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
    })

    it('hides a tier with no demos in it', async () => {
        vi.spyOn(api, 'listDemos').mockResolvedValue([
            { run_id: 'bbb', title: 'Fox', prompt: 'a fox game', tier: 'oneshot' },
        ])
        renderLanding()
        await waitFor(() => screen.getByText('a fox game'))
        expect(screen.queryByText("What's possible")).toBeNull()
    })
})
