import React, { useState, useEffect, useRef } from 'react'
import ReactMarkdown from 'react-markdown'
import { useTaskSocket } from '../hooks/useTaskSocket'
import { api } from '../api/client'
import type { TaskDetail, Subtask, ToolUsage, AgentMessage } from '../types'
import AgentCard from './AgentCard'

interface StagePanelProps {
    taskId: string | null
}

interface SubtaskState {
    id: string
    agentId: string
    goal: string
    status: 'pending' | 'in_progress' | 'completed' | 'failed'
    outputPreview: string | null
    position: number
}

const toSubtaskState = (s: Subtask): SubtaskState => ({
    id: s.id,
    agentId: s.agent_id,
    goal: s.goal,
    status: s.status as SubtaskState['status'],
    outputPreview: s.output_preview,
    position: s.position,
})

type WSMessage = ReturnType<typeof useTaskSocket>['messages'][number]

const mergeSubtasks = (base: SubtaskState[], messages: WSMessage[]): SubtaskState[] => {
    if (base.length === 0) return base
    return base.map(s => {
        let result = s
        for (const msg of messages) {
            if (msg.type === 'subtask_started' && msg.subtask_id === s.id) {
                result = { ...result, status: 'in_progress' }
            } else if (msg.type === 'subtask_completed' && msg.subtask_id === s.id) {
                result = { ...result, status: 'completed' }
            } else if (msg.type === 'subtask_failed' && msg.subtask_id === s.id) {
                result = { ...result, status: 'failed' }
            }
        }
        return result
    })
}

const statusLabel = (status: string) => ({
    pending: { label: 'Pending', color: 'bg-gray-700 text-gray-300' },
    planning: { label: 'Planning', color: 'bg-yellow-900/50 text-yellow-400' },
    in_progress: { label: 'In Progress', color: 'bg-yellow-900/50 text-yellow-400' },
    completed: { label: 'Completed', color: 'bg-green-900/50 text-green-400' },
    failed: { label: 'Failed', color: 'bg-red-900/50 text-red-400' },
}[status] ?? { label: status, color: 'bg-gray-700 text-gray-300' })

// Separate component so it can manage its own mount animation
const FadeSlideIn: React.FC<{
    children: React.ReactNode
    delay?: number
    className?: string
}> = ({ children, delay = 0, className = '' }) => {
    const [visible, setVisible] = useState(false)

    useEffect(() => {
        const t = setTimeout(() => setVisible(true), delay)
        return () => clearTimeout(t)
    }, [delay])

    return (
        <div
            className={`transition-all duration-500 ease-out ${visible ? 'opacity-100 translate-y-0' : 'opacity-0 translate-y-3'
                } ${className}`}
        >
            {children}
        </div>
    )
}

const StagePanel: React.FC<StagePanelProps> = ({ taskId }) => {
    const { messages } = useTaskSocket(taskId)
    const [task, setTask] = useState<TaskDetail | null>(null)
    const [baseSubtasks, setBaseSubtasks] = useState<SubtaskState[]>([])
    const [artifact, setArtifact] = useState<string | null>(null)
    // Track the phase so we can crossfade between live → done
    const [phase, setPhase] = useState<'idle' | 'live' | 'completing' | 'done' | 'failed'>('idle')
    const [agentsExpanded, setAgentsExpanded] = useState(false)
    const prevTaskId = useRef<string | null>(null)

    // New state for tool usage and agent messages
    const [expandedSubtasks, setExpandedSubtasks] = useState<Set<string>>(new Set())
    const [toolUsageBySubtask, setToolUsageBySubtask] = useState<Map<string, ToolUsage[]>>(new Map())
    const [agentMessagesBySubtask, setAgentMessagesBySubtask] = useState<Map<string, AgentMessage[]>>(new Map())
    const [maestroExpanded, setMaestroExpanded] = useState(false)

    const subtasks = mergeSubtasks(baseSubtasks, messages)
    const isPlanning = task?.status === 'planning' && subtasks.length === 0

    const loadArtifact = async (tid: string, contextKeys: string[]) => {
        const finalKey = contextKeys[contextKeys.length - 1]
        try {
            const content = await api.getContextValue(tid, finalKey)
            setArtifact(content)
            // Brief pause so the live view can fade out first, then flip to done
            setTimeout(() => setPhase('done'), 150)
        } catch (e) {
            console.error('Failed to load artifact:', e)
            setPhase('done')
        }
    }

    // Reset + fetch on taskId change
    useEffect(() => {
        if (!taskId) {
            setTask(null)
            setBaseSubtasks([])
            setArtifact(null)
            setPhase('idle')
            setAgentsExpanded(false)
            prevTaskId.current = null
            return
        }

        // Switching to a different task — reset phase immediately
        if (prevTaskId.current !== taskId) {
            setPhase('idle')
            setArtifact(null)
            setAgentsExpanded(false)
            prevTaskId.current = taskId
        }

        api.getTask(taskId)
            .then(detail => {
                setTask(detail)
                setBaseSubtasks(detail.subtasks.map(toSubtaskState))

                if (detail.status === 'completed') {
                    if (detail.context_keys.length > 0) {
                        loadArtifact(taskId, detail.context_keys)
                    } else {
                        setPhase('done')
                    }
                } else if (detail.status === 'failed') {
                    setPhase('failed')
                } else {
                    setPhase('live')
                }
            })
            .catch(() => {
                setTask(null)
                setBaseSubtasks([])
                setPhase('idle')
            })
    }, [taskId])

    // React to WS messages
    useEffect(() => {
        const latest = messages[messages.length - 1]
        if (!latest || !taskId) return

        if (latest.type === 'task_status' || latest.type === 'subtask_started') {
            api.getTask(taskId)
                .then(detail => {
                    setTask(detail)
                    if (detail.subtasks.length > 0) {
                        setBaseSubtasks(detail.subtasks.map(toSubtaskState))
                    }
                    if (phase === 'idle') setPhase('live')
                })
                .catch(console.error)
        }

        if (latest.type === 'task_completed' || latest.type === 'task_failed') {
            // Start the completing transition — fades out the live view
            setPhase('completing')

            api.getTask(taskId)
                .then(detail => {
                    setTask(detail)
                    setBaseSubtasks(detail.subtasks.map(toSubtaskState))

                    if (detail.status === 'completed' && detail.context_keys.length > 0) {
                        loadArtifact(taskId, detail.context_keys)
                    } else if (detail.status === 'failed') {
                        setTimeout(() => setPhase('failed'), 150)
                    } else {
                        setTimeout(() => setPhase('done'), 150)
                    }
                })
                .catch(console.error)
        }
    }, [messages, taskId])

    // Process tool_usage and agent_message events
    useEffect(() => {
        const toolUsage = new Map<string, ToolUsage[]>()
        const agentMsgs = new Map<string, AgentMessage[]>()

        messages.forEach(msg => {
            if (msg.type === 'tool_usage') {
                const existing = toolUsage.get(msg.subtask_id) || []
                toolUsage.set(msg.subtask_id, [...existing, {
                    tool_name: msg.tool_name,
                    arguments: msg.arguments,
                    status: msg.status,
                    timestamp: msg.timestamp
                }])
            } else if (msg.type === 'agent_message' && msg.subtask_id) {
                const existing = agentMsgs.get(msg.subtask_id) || []
                agentMsgs.set(msg.subtask_id, [...existing, {
                    agent_id: msg.agent_id,
                    phase: msg.phase,
                    message: msg.message,
                    timestamp: msg.timestamp
                }])
            }
        })

        setToolUsageBySubtask(toolUsage)
        setAgentMessagesBySubtask(agentMsgs)
    }, [messages])

    // ── Empty state ──────────────────────────────────────────────────────────
    if (!taskId) {
        return (
            <div className="h-full flex items-center justify-center">
                <p className="text-sm text-gray-600">Create a task to get started</p>
            </div>
        )
    }

    const { label, color } = statusLabel(task?.status ?? 'pending')
    const isDone = phase === 'done' || phase === 'failed'
    // Live view fades out during 'completing', done view fades in during 'done'/'failed'
    const liveVisible = phase === 'live' || phase === 'completing'
    const doneVisible = phase === 'done' || phase === 'failed'

    return (
        <div className="h-full flex flex-col overflow-y-auto">

            {/* ── Done / Failed view ────────────────────────────────────────── */}
            <div
                className={`transition-all duration-500 ease-out ${doneVisible ? 'opacity-100 translate-y-0' : 'opacity-0 -translate-y-2 pointer-events-none'
                    }`}
                aria-hidden={!doneVisible}
            >
                {phase === 'done' && artifact ? (
                    <div className="border-b border-white/10">
                        {/* Completion header */}
                        <FadeSlideIn delay={0}>
                            <div className="px-6 py-4 flex items-center gap-3 border-b border-green-500/20 bg-green-500/5">
                                <div className="w-2 h-2 rounded-full bg-green-500 shadow-[0_0_8px_rgba(34,197,94,0.8)]" />
                                <span className="text-sm font-medium text-green-400">Complete</span>
                                <span className="text-sm text-gray-500 ml-auto truncate max-w-xs">
                                    {task?.goal}
                                </span>
                            </div>
                        </FadeSlideIn>

                        {/* Artifact body — explicit color overrides so dark bg never gets light prose defaults */}
                        <FadeSlideIn delay={80}>
                            <div className="px-6 py-6 [&_*]:!text-inherit text-gray-200
                                [&_h1]:text-white [&_h1]:font-semibold [&_h1]:text-xl [&_h1]:mb-3 [&_h1]:mt-5
                                [&_h2]:text-white [&_h2]:font-semibold [&_h2]:text-lg [&_h2]:mb-2 [&_h2]:mt-4
                                [&_h3]:text-white [&_h3]:font-medium [&_h3]:text-base [&_h3]:mb-2 [&_h3]:mt-3
                                [&_p]:text-gray-300 [&_p]:leading-relaxed [&_p]:mb-3
                                [&_strong]:text-white [&_strong]:font-semibold
                                [&_em]:text-gray-300 [&_em]:italic
                                [&_ul]:text-gray-300 [&_ul]:pl-5 [&_ul]:mb-3 [&_ul]:list-disc
                                [&_ol]:text-gray-300 [&_ol]:pl-5 [&_ol]:mb-3 [&_ol]:list-decimal
                                [&_li]:text-gray-300 [&_li]:mb-1
                                [&_li::marker]:text-gray-500
                                [&_blockquote]:border-l-2 [&_blockquote]:border-gray-600 [&_blockquote]:pl-4 [&_blockquote]:text-gray-400 [&_blockquote]:italic [&_blockquote]:my-3
                                [&_code]:text-blue-300 [&_code]:bg-white/5 [&_code]:px-1.5 [&_code]:py-0.5 [&_code]:rounded [&_code]:text-sm
                                [&_pre]:bg-white/5 [&_pre]:rounded-lg [&_pre]:p-4 [&_pre]:mb-3 [&_pre]:overflow-x-auto
                                [&_pre_code]:bg-transparent [&_pre_code]:p-0 [&_pre_code]:text-blue-300
                                [&_hr]:border-white/10 [&_hr]:my-4
                                [&_a]:text-blue-400 [&_a]:underline [&_a:hover]:text-blue-300
                                [&_table]:w-full [&_table]:text-sm [&_table]:mb-3
                                [&_th]:text-gray-200 [&_th]:font-semibold [&_th]:text-left [&_th]:pb-2 [&_th]:border-b [&_th]:border-white/10
                                [&_td]:text-gray-300 [&_td]:py-2 [&_td]:border-b [&_td]:border-white/5
                            ">
                                <ReactMarkdown>{artifact}</ReactMarkdown>
                            </div>
                        </FadeSlideIn>
                    </div>
                ) : phase === 'failed' ? (
                    <FadeSlideIn>
                        <div className="px-6 py-4 border-b border-red-500/20 bg-red-500/5 flex items-center gap-3">
                            <div className="w-2 h-2 rounded-full bg-red-500" />
                            <span className="text-sm font-medium text-red-400">Task failed</span>
                        </div>
                    </FadeSlideIn>
                ) : null}

                {/* Collapsible agent cards */}
                {isDone && subtasks.length > 0 && (
                    <FadeSlideIn delay={160}>
                        <div className="border-b border-white/[0.06]">
                            <button
                                className="w-full px-6 py-3 flex items-center gap-2 text-left hover:bg-white/[0.02] transition-colors"
                                onClick={() => setAgentsExpanded(prev => !prev)}
                            >
                                <span className={`text-gray-600 text-xs transition-transform duration-200 ${agentsExpanded ? 'rotate-90' : ''}`}>
                                    ▶
                                </span>
                                <span className="text-xs text-gray-600 hover:text-gray-400 transition-colors">
                                    How this was made
                                </span>
                            </button>

                            {/* Animated expand/collapse */}
                            <div className={`grid transition-all duration-300 ease-in-out ${agentsExpanded ? 'grid-rows-[1fr] opacity-100' : 'grid-rows-[0fr] opacity-0'
                                }`}>
                                <div className="overflow-hidden">
                                    <div className="px-6 pb-4 flex flex-col gap-3">
                                        {[...subtasks]
                                            .sort((a, b) => a.position - b.position)
                                            .map((subtask, i) => (
                                                <FadeSlideIn key={subtask.id} delay={agentsExpanded ? i * 60 : 0}>
                                                    <AgentCard
                                                        agentId={subtask.agentId}
                                                        goal={subtask.goal}
                                                        status={subtask.status}
                                                        outputPreview={subtask.outputPreview}
                                                        position={subtask.position}
                                                        animationDelay={0}
                                                        toolUsage={toolUsageBySubtask.get(subtask.id)}
                                                        agentMessages={agentMessagesBySubtask.get(subtask.id)}
                                                        isExpanded={expandedSubtasks.has(subtask.id)}
                                                        onToggleExpand={() => {
                                                            setExpandedSubtasks(prev => {
                                                                const next = new Set(prev)
                                                                if (next.has(subtask.id)) {
                                                                    next.delete(subtask.id)
                                                                } else {
                                                                    next.add(subtask.id)
                                                                }
                                                                return next
                                                            })
                                                        }}
                                                    />
                                                </FadeSlideIn>
                                            ))
                                        }
                                    </div>
                                </div>
                            </div>
                        </div>
                    </FadeSlideIn>
                )}
            </div>

            {/* ── Live / In-progress view ───────────────────────────────────── */}
            <div
                className={`flex flex-col gap-5 p-6 transition-all duration-400 ease-in-out ${liveVisible ? 'opacity-100 translate-y-0' : 'opacity-0 translate-y-2 pointer-events-none'
                    }`}
                aria-hidden={!liveVisible}
            >
                {/* Task header */}
                {task && (
                    <div>
                        <p className="text-white font-medium leading-snug mb-3">{task.goal}</p>
                        <div className="flex items-center gap-2">
                            {(phase === 'live') && (
                                <div className="w-1.5 h-1.5 rounded-full bg-green-500 animate-pulse" />
                            )}
                            <span className={`text-xs px-2 py-0.5 rounded-full font-medium ${color}`}>
                                {label}
                            </span>
                            <span className="text-xs px-2 py-0.5 rounded-full bg-white/5 text-gray-400 capitalize">
                                {task.execution_mode}
                            </span>
                        </div>
                    </div>
                )}

                {/* Planning indicator */}
                {isPlanning && (
                    <div className="flex items-center gap-3 py-2">
                        <div className="flex gap-1">
                            <div className="w-1.5 h-1.5 rounded-full bg-blue-500 animate-bounce" style={{ animationDelay: '0ms' }} />
                            <div className="w-1.5 h-1.5 rounded-full bg-blue-500 animate-bounce" style={{ animationDelay: '150ms' }} />
                            <div className="w-1.5 h-1.5 rounded-full bg-blue-500 animate-bounce" style={{ animationDelay: '300ms' }} />
                        </div>
                        <span className="text-sm text-gray-500">Planning...</span>
                    </div>
                )}

                {/* Maestro activity section */}
                {(() => {
                    const maestroMessages = messages.filter((m): m is Extract<typeof m, { type: 'agent_message' }> =>
                        m.type === 'agent_message' && m.agent_id === 'maestro'
                    )
                    return maestroMessages.length > 0 && (
                        <div className="border border-white/5 bg-white/[0.02] rounded-lg p-3">
                            <button
                                className="w-full flex items-center gap-2 text-left"
                                onClick={() => setMaestroExpanded(prev => !prev)}
                            >
                                <span className={`text-gray-600 text-xs transition-transform duration-200 ${maestroExpanded ? 'rotate-90' : ''}`}>
                                    ▶
                                </span>
                                <span className="text-xs text-gray-500">Orchestrator activity</span>
                                <span className="ml-auto text-xs text-gray-600">
                                    {maestroMessages.length} updates
                                </span>
                            </button>

                            {maestroExpanded && (
                                <div className="mt-3 space-y-2 border-t border-white/10 pt-3">
                                    {maestroMessages.map((msg, idx) => (
                                        <div key={idx} className="text-xs bg-white/5 p-2 rounded">
                                            <div className="flex items-center gap-2 mb-1">
                                                <span className="text-purple-400">{msg.phase}</span>
                                                <span className="text-gray-600 text-[10px]">
                                                    {new Date(msg.timestamp).toLocaleTimeString()}
                                                </span>
                                            </div>
                                            <p className="text-gray-400 whitespace-pre-wrap">{msg.message}</p>
                                        </div>
                                    ))}
                                </div>
                            )}
                        </div>
                    )
                })()}

                {/* Agent cards — staggered entrance */}
                {subtasks.length > 0 && (
                    <div className="flex flex-col gap-3">
                        {[...subtasks]
                            .sort((a, b) => a.position - b.position)
                            .map(subtask => (
                                <AgentCard
                                    key={subtask.id}
                                    agentId={subtask.agentId}
                                    goal={subtask.goal}
                                    status={subtask.status}
                                    outputPreview={subtask.outputPreview}
                                    position={subtask.position}
                                    animationDelay={subtask.position * 80}
                                    toolUsage={toolUsageBySubtask.get(subtask.id)}
                                    agentMessages={agentMessagesBySubtask.get(subtask.id)}
                                    isExpanded={expandedSubtasks.has(subtask.id)}
                                    onToggleExpand={() => {
                                        setExpandedSubtasks(prev => {
                                            const next = new Set(prev)
                                            if (next.has(subtask.id)) {
                                                next.delete(subtask.id)
                                            } else {
                                                next.add(subtask.id)
                                            }
                                            return next
                                        })
                                    }}
                                />
                            ))
                        }
                    </div>
                )}
            </div>
        </div>
    )
}

export default StagePanel