import React, { useEffect, useState } from 'react'

interface MaestroCardProps {
    phase: 'planning' | 'evaluation' | 'error_recovery'
    message: string
    timestamp: string
    isExpanded: boolean
    onToggleExpand: () => void
}

const MaestroCard: React.FC<MaestroCardProps> = ({
    phase,
    message,
    timestamp,
    isExpanded,
    onToggleExpand,
}) => {
    const [visible, setVisible] = useState(false)

    useEffect(() => {
        const t = setTimeout(() => setVisible(true), 0)
        return () => clearTimeout(t)
    }, [])

    const phaseConfig = {
        planning: {
            color: 'border-l-blue-500',
            bg: 'bg-blue-500/5',
            glow: 'shadow-[0_0_12px_rgba(59,130,246,0.15)]',
            label: 'Planning'
        },
        evaluation: {
            color: 'border-l-yellow-500',
            bg: 'bg-yellow-500/5',
            glow: 'shadow-[0_0_12px_rgba(234,179,8,0.15)]',
            label: 'Evaluating'
        },
        error_recovery: {
            color: 'border-l-red-500',
            bg: 'bg-red-500/5',
            glow: 'shadow-[0_0_12px_rgba(239,68,68,0.15)]',
            label: 'Recovering'
        }
    }[phase]

    const formatTimestamp = (ts: string) => {
        try {
            return new Date(ts).toLocaleTimeString()
        } catch {
            return ts
        }
    }

    return (
        <div
            className={`
                border border-white/[0.06] border-l-4 ${phaseConfig.color}
                ${phaseConfig.bg} rounded-lg p-4 transition-all duration-500
                ${phaseConfig.glow}
                ${visible ? 'opacity-100 translate-y-0' : 'opacity-0 translate-y-2'}
            `}
        >
            <div className="flex items-center justify-between mb-2">
                <div className="flex items-center gap-3">
                    <span className="text-base leading-none">🎭</span>
                    <span className="text-sm font-medium text-white">
                        Maestro • {phaseConfig.label}
                    </span>
                </div>
                <span className="text-xs text-gray-500">
                    {formatTimestamp(timestamp)}
                </span>
            </div>

            {!isExpanded && (
                <div className="pl-8">
                    <p className="text-xs text-gray-400 line-clamp-2">
                        {message}
                    </p>
                </div>
            )}

            {isExpanded && (
                <div className="pl-8 mt-2">
                    <div className="text-xs text-gray-300 max-h-[300px] overflow-y-auto whitespace-pre-wrap">
                        {message}
                    </div>
                </div>
            )}

            <button
                onClick={onToggleExpand}
                className="mt-3 ml-8 text-xs text-blue-400 hover:text-blue-300 transition-colors"
            >
                {isExpanded ? '▲ Hide reasoning' : '▼ View reasoning'}
            </button>
        </div>
    )
}

export default MaestroCard
