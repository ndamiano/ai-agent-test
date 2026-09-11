import { cleanup, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it } from 'vitest'
import { StockoutLines } from './QueuesPage'
import type { Stockouts } from '../../types'

const none: Stockouts = {
    last_1h: 0,
    last_24h: 0,
    last_7d: 0,
    last_at: null,
}

describe('StockoutLines', () => {
    afterEach(cleanup)

    it('shows the outage under way and the week history', () => {
        render(<StockoutLines s={{ ...none, last_1h: 3, last_24h: 3, last_7d: 12, last_at: 1000 }} now={1000} />)
        expect(screen.getByText(/Out of stock now/)).toBeTruthy()
        expect(screen.getByText(/12 stock-outs in the last 7 days, last/)).toBeTruthy()
    })

    it('shows only history once the outage is over', () => {
        render(<StockoutLines s={{ ...none, last_7d: 1, last_at: 0 }} now={1000} />)
        expect(screen.queryByText(/Out of stock now/)).toBeNull()
        expect(screen.getByText(/1 stock-out in the last 7 days/)).toBeTruthy()
    })
})
