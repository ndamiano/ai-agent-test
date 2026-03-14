import React, { useEffect, useState } from 'react'
import type { ToolUsage, AgentMessage } from '../types'

interface AgentCardProps {
    agentId: string
    goal: string
    status: 'pending' | 'in_progress' | 'completed' | 'failed'
    outputPreview: string | null
    position: number
    animationDelay: number
    toolUsage?: ToolUsage[]
    agentMessages?: AgentMessage[]
    isExpanded?: boolean
    onToggleExpand?: () => void
}

const formatAgentName = (agentId: string) =>
    agentId
        .replace(/[-_]/g, ' ')
        .replace(/\b\w/g, c => c.toUpperCase())

const AgentCard: React.FC<AgentCardProps> = ({
    agentId,
    goal: _goal,
    status,
    outputPreview,
    position: _position,
    animationDelay,
    toolUsage,
    agentMessages,
    isExpanded = false,
    onToggleExpand,
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

            {/* Tool usage badge when collapsed */}
            {!isExpanded && toolUsage && toolUsage.length > 0 && onToggleExpand && (
                <div className="mt-3 flex items-center gap-2 pl-8">
                    <span className="text-xs text-gray-500 bg-white/5 px-2 py-1 rounded">
                        {toolUsage.length} tool{toolUsage.length > 1 ? 's' : ''} used
                    </span>
                    <button
                        onClick={onToggleExpand}
                        className="text-xs text-blue-400 hover:text-blue-300 transition-colors"
                    >
                        View details ▼
                    </button>
                </div>
            )}

            {/* Expandable details section */}
            {isExpanded && onToggleExpand && (
                <div className="mt-3 space-y-3 border-t border-white/10 pt-3">
                    {/* Agent Messages */}
                    {agentMessages && agentMessages.length > 0 && (
                        <div>
                            <h4 className="text-xs font-medium text-gray-400 mb-2">Agent Reasoning</h4>
                            {agentMessages.map((msg, idx) => (
                                <div key={idx} className="text-xs text-gray-500 mb-2 bg-white/5 p-2 rounded">
                                    <div className="flex items-center gap-2 mb-1">
                                        <span className="text-blue-400">{msg.phase}</span>
                                        <span className="text-gray-600 text-[10px]">
                                            {new Date(msg.timestamp).toLocaleTimeString()}
                                        </span>
                                    </div>
                                    <p className="text-gray-400 whitespace-pre-wrap">{msg.message}</p>
                                </div>
                            ))}
                        </div>
                    )}

                    {/* Tool Usage */}
                    {toolUsage && toolUsage.length > 0 && (
                        <div>
                            <h4 className="text-xs font-medium text-gray-400 mb-2">Tools Used</h4>
                            <div className="space-y-1">
                                {toolUsage.map((tool, idx) => (
                                    <div
                                        key={idx}
                                        className="text-xs bg-white/5 p-2 rounded flex items-start gap-2"
                                    >
                                        <span className={tool.status === 'success' ? 'text-green-500' : 'text-red-500'}>
                                            {tool.status === 'success' ? '✓' : '✗'}
                                        </span>
                                        <div className="flex-1">
                                            <div className="text-gray-300 font-medium">{tool.tool_name}</div>
                                            <div className="text-gray-600 text-[10px] mt-1">
                                                {Object.keys(tool.arguments).length > 0 ? (
                                                    <details className="cursor-pointer">
                                                        <summary>Arguments</summary>
                                                        <pre className="mt-1 text-[9px] overflow-x-auto">
                                                            {JSON.stringify(tool.arguments, null, 2)}
                                                        </pre>
                                                    </details>
                                                ) : (
                                                    'No arguments'
                                                )}
                                            </div>
                                        </div>
                                    </div>
                                ))}
                            </div>
                        </div>
                    )}

                    <button
                        onClick={onToggleExpand}
                        className="text-xs text-gray-600 hover:text-gray-400 w-full text-center transition-colors"
                    >
                        Hide details ▲
                    </button>
                </div>
            )}
        </div>
    )
}

export default AgentCard