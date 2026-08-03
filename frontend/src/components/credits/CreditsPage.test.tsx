import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import CreditsPage, { usd } from './CreditsPage'
import { AuthProvider } from '../../contexts/AuthContext'
import { RouterProvider } from '../../router'
import { api, setAuthToken } from '../../api/client'

afterEach(() => { cleanup(); vi.restoreAllMocks(); setAuthToken(null) })

const PACKAGES = [
    { id: '1', credits: 1, usd_cents: 500 },
    { id: '5', credits: 5, usd_cents: 2500 },
    { id: '10', credits: 10, usd_cents: 5000 },
]

const me = (balance: number) =>
    ({ id: 'u1', handle: 'alice', role: 'user', balance })

const mount = () => render(
    <RouterProvider>
        <AuthProvider>
            <CreditsPage />
        </AuthProvider>
    </RouterProvider>,
)

describe('CreditsPage', () => {
    it('renders the package catalog with exact prices', async () => {
        vi.spyOn(api, 'listPackages').mockResolvedValue(PACKAGES)
        vi.spyOn(api, 'listPurchases').mockResolvedValue([])
        mount()

        await waitFor(() => expect(screen.getAllByRole('button', { name: 'Buy' })).toHaveLength(3))
        expect(screen.getByText('$5')).toBeTruthy()
        expect(screen.getByText('$25')).toBeTruthy()
        expect(screen.getByText('$50')).toBeTruthy()
        expect(screen.getByText(/no purchases yet/i)).toBeTruthy()
    })

    it('buying runs start→complete, refreshes the balance, and lists the purchase', async () => {
        setAuthToken('tok')
        vi.spyOn(api, 'me')
            .mockResolvedValueOnce(me(0))
            .mockResolvedValue(me(5))
        vi.spyOn(api, 'listPackages').mockResolvedValue(PACKAGES)
        vi.spyOn(api, 'listPurchases')
            .mockResolvedValueOnce([])
            .mockResolvedValue([{
                id: 'p1', package_id: '5', credits: 5, usd_cents: 2500,
                status: 'completed', created_at: 1754200000, completed_at: 1754200001,
            }])
        const start = vi.spyOn(api, 'startPurchase')
            .mockResolvedValue({ purchase_id: 'p1', status: 'started' })
        const complete = vi.spyOn(api, 'completePurchase')
            .mockResolvedValue({ status: 'completed', credits: 5, balance: 5 })
        mount()

        await waitFor(() => expect(screen.getAllByRole('button', { name: 'Buy' })).toHaveLength(3))
        fireEvent.click(screen.getAllByRole('button', { name: 'Buy' })[1])

        await waitFor(() => expect(screen.getByText(/5 credits added/i)).toBeTruthy())
        expect(start).toHaveBeenCalledWith('5')
        expect(complete).toHaveBeenCalledWith('p1')
        expect(api.me).toHaveBeenCalledTimes(2)
        await waitFor(() => expect(screen.getByText('5 credits')).toBeTruthy())
        expect(screen.getAllByText('$25')).toHaveLength(2)
        expect(screen.queryByText(/no purchases yet/i)).toBeNull()
    })

    it('says what went wrong when the purchase fails', async () => {
        vi.spyOn(api, 'listPackages').mockResolvedValue(PACKAGES)
        vi.spyOn(api, 'listPurchases').mockResolvedValue([])
        vi.spyOn(api, 'startPurchase').mockRejectedValue(new Error('unknown package'))
        mount()

        await waitFor(() => expect(screen.getAllByRole('button', { name: 'Buy' })).toHaveLength(3))
        fireEvent.click(screen.getAllByRole('button', { name: 'Buy' })[0])

        await waitFor(() => expect(screen.getByText(/unknown package/i)).toBeTruthy())
    })
})

describe('usd', () => {
    it('shows whole dollars without cents and fractional exactly', () => {
        expect(usd(500)).toBe('$5')
        expect(usd(5000)).toBe('$50')
        expect(usd(550)).toBe('$5.50')
    })
})
