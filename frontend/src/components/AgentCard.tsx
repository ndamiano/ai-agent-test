import React, { useEffect, useState } from 'react'

interface AgentCardProps {
    agentId: string
    goal: string
    status: 'pending' | 'in_progress' | 'completed' | 'failed'
    outputPreview: string | null
    position: number
    animationDelay: number
}

const formatAgentName = (agentId: string) =>
    agentId
        .replace(/[-_]/g, ' ')
        .replace(/\b\w/g, c => c.toUpperCase())

const AgentCard: React.FC<AgentCardProps> = ({
    agentId,
    goal,
    status,
    outputPreview,
    position,
    animationDelay,
}) => {
    const [visible, setVisible] = useState(false)

    useEffect(() => {
        const t = setTimeout(() => setVisible(true), animationDelay)
        return () => clearTimeout(t)
    }, [animationDelay])

    const borderColor = {
        pending: 'border-l-white/10',
        in_progress: 'border-l-blue-500',
        completed: 'border-l-green-500',
        failed: 'border-l-red-500',
    }[status]

    const icon = {
        pending: <span className="text-gray-600">⏳</span>,
        in_progress: <span className="animate-spin inline-block">🔄</span>,
        completed: <span>✅</span>,
        failed: <span>❌</span>,
    }[status]

    const subtext = () => {
        if (status === 'pending') return (
            <span className="text-gray-600 text-xs">Waiting...</span>
        )
        if (status === 'in_progress') return (
            <span className="text-gray-500 text-xs">Working...</span>
        )
        if (status === 'failed') return (
            <span className="text-red-500/70 text-xs">Something went wrong</span>
        )
        if (status === 'completed') return (
            <span className="text-gray-400 text-xs line-clamp-2">
                {outputPreview ?? 'Completed'}
            </span>
        )
    }

    return (
        <div
            className={`
                border border-white/[0.06] border-l-4 ${borderColor}
                bg-white/[0.03] rounded-lg p-4 transition-all duration-500
                ${status === 'in_progress' ? 'shadow-[0_0_12px_rgba(59,130,246,0.15)]' : ''}
                ${visible ? 'opacity-100 translate-y-0' : 'opacity-0 translate-y-2'}
            `}
            style={{ transitionDelay: visible ? '0ms' : `${animationDelay}ms` }}
        >
            <div className="flex items-center gap-3 mb-1">
                <span className="text-base leading-none">{icon}</span>
                <span className={`text-sm font-medium ${status === 'pending' ? 'text-gray-500' : 'text-white'}`}>
                    {formatAgentName(agentId)}
                </span>
            </div>

            <div className="pl-8">
                {subtext()}
            </div>

            {status === 'in_progress' && (
                <div className="mt-3 h-[3px] rounded-full overflow-hidden bg-white/5">
                    <div className="shimmer h-full w-full" />
                </div>
            )}
        </div>
    )
}

export default AgentCard