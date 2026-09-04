import { cleanup, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it } from 'vitest'
import { StockoutLines } from './QueuesPage'
import type { Stockouts } from '../../types'

const zero = { attempts: 0, stock_refusals: 0, other_refusals: 0, since: null }
const none: Stockouts = {
    totals_60d: zero, totals_all: zero,
    last_1h: 0, last_24h: 0, last_7d: 0, last_at: null, last_error: null,
    active: false, active_since: null, active_count: 0,
}

describe('StockoutLines', () => {
    afterEach(cleanup)

    it('renders nothing when the provider never refused for stock', () => {
        const { container } = render(<StockoutLines s={none} />)
        expect(container.innerHTML).toBe("")
    })

    it('shows the outage under way and the week history', () => {
        const now = Date.now() / 1000
        render(<StockoutLines s={{ ...none, last_1h: 3, last_24h: 3, last_7d: 12, last_at: now,
            last_error: 'There are no instances currently available',
            active: true, active_since: now - 120, active_count: 3 }} />)
        expect(screen.getByText(/Out of stock now — 3 refusals since/).textContent)
            .toContain("(last: 'There are no instances currently available')")
        expect(screen.getByText(/12 stock-outs in the last 7 days, last/)).toBeTruthy()
    })

    it('states the provider-facing ratio whenever a pod was ever requested', () => {
        render(<StockoutLines s={{ ...none,
            totals_60d: { attempts: 1203, stock_refusals: 418, other_refusals: 2, since: '2026-08-01' },
            totals_all: { attempts: 1900, stock_refusals: 512, other_refusals: 3, since: '2026-06-01' } }} />)
        expect(screen.getByText(/Last 60 days/).textContent).toBe(
            'Last 60 days: 418 of 1,203 pod requests refused for stock · all time since 2026-06-01: 512 of 1,900 pod requests refused for stock')
    })

    it('shows only history once the outage is over', () => {
        render(<StockoutLines s={{ ...none, last_7d: 1, last_at: Date.now() / 1000 }} />)
        expect(screen.queryByText(/Out of stock now/)).toBeNull()
        expect(screen.getByText(/1 stock-out in the last 7 days/)).toBeTruthy()
    })
})
