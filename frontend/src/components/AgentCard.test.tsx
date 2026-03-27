import { describe, it, expect } from 'vitest'
import { render } from '@testing-library/react'
import AgentCard from './AgentCard'

const defaultProps = {
    agentId: 'test-agent',
    animationDelay: 0,
}

describe('AgentCard status icons', () => {
    it('renders SVG icon for pending status', () => {
        const { container } = render(<AgentCard {...defaultProps} status="pending" />)
        const svg = container.querySelector('svg')
        expect(svg).toBeTruthy()
    })

    it('renders Loader2 icon with spin class for in_progress status', () => {
        const { container } = render(<AgentCard {...defaultProps} status="in_progress" />)
        const svg = container.querySelector('svg')
        expect(svg).toBeTruthy()
        expect(svg?.classList.contains('animate-spin')).toBe(true)
    })

    it('renders CheckCircle2 icon for completed status', () => {
        const { container } = render(<AgentCard {...defaultProps} status="completed" />)
        const svg = container.querySelector('svg')
        expect(svg).toBeTruthy()
        expect(svg?.classList.contains('text-green-500')).toBe(true)
    })

    it('renders XCircle icon for failed status', () => {
        const { container } = render(<AgentCard {...defaultProps} status="failed" />)
        const svg = container.querySelector('svg')
        expect(svg).toBeTruthy()
        expect(svg?.classList.contains('text-red-500')).toBe(true)
    })

    it('does not render emoji characters in any status', () => {
        const statuses = ['pending', 'in_progress', 'completed', 'failed'] as const
        for (const status of statuses) {
            const { container } = render(<AgentCard {...defaultProps} status={status} />)
            const iconSpan = container.querySelector('.text-base.leading-none')
            expect(iconSpan?.textContent).toBe('')
        }
    })
})
