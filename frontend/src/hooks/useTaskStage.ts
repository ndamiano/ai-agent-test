import { useState, useEffect, useRef, useCallback, useMemo } from 'react'
import { useTaskSocket } from './useTaskSocket'
import { api } from '../api/client'
import type { TaskDetail, Subtask, ToolUsage, AgentMessage, ArtifactManifest } from '../types'

export type SubtaskStatus = 'pending' | 'in_progress' | 'completed' | 'failed'

export interface SubtaskState {
    id: string
    agentId: string
    goal: string
    name: string | null
    description: string | null
    status: SubtaskStatus
    outputPreview: string | null
    position: number
}

export const VALID_SUBTASK_STATUSES: readonly SubtaskStatus[] = [
    'pending',
    'in_progress',
    'completed',
    'failed',
] as const

export function toSubtaskState(s: Subtask): SubtaskState {
    const status = (VALID_SUBTASK_STATUSES as readonly string[]).includes(s.status)
        ? s.status
        : (console.warn(`Unrecognized subtask status: "${s.status}", defaulting to "pending"`), 'pending')
    return {
        id: s.id,
        agentId: s.agent_id,
        goal: s.goal,
        name: s.name,
        description: s.description,
        status: status as SubtaskStatus,
        outputPreview: s.output_preview,
        position: s.position,
    }
}

type WSMessage = ReturnType<typeof useTaskSocket>['messages'][number]

export const mergeSubtasks = (base: SubtaskState[], messages: WSMessage[]): SubtaskState[] => {
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

export type Phase = 'idle' | 'live' | 'completing' | 'done' | 'failed'

export interface TaskStageState {
    task: TaskDetail | null
    subtasks: SubtaskState[]
    artifact: ArtifactManifest | null
    phase: Phase
    isPlanning: boolean
    loading: boolean
    retrying: boolean
    expandedSubtasks: Set<string>
    toolUsageBySubtask: Map<string, ToolUsage[]>
    agentMessagesBySubtask: Map<string, AgentMessage[]>
    maestroMessage: { phase: string; message: string; timestamp: string } | null
    maestroExpanded: boolean
    agentsExpanded: boolean
    toggleSubtask: (id: string) => void
    toggleMaestro: () => void
    toggleAgents: () => void
    retry: () => void
}

export function useTaskStage(taskId: string | null): TaskStageState {
    const { messages } = useTaskSocket(taskId)
    const [task, setTask] = useState<TaskDetail | null>(null)
    const [baseSubtasks, setBaseSubtasks] = useState<SubtaskState[]>([])
    const [artifact, setArtifact] = useState<ArtifactManifest | null>(null)
    const [phase, setPhase] = useState<Phase>('idle')
    const [loading, setLoading] = useState(false)
    const [agentsExpanded, setAgentsExpanded] = useState(false)
    const prevTaskId = useRef<string | null>(null)
    const processedCountRef = useRef(0)

    const [expandedSubtasks, setExpandedSubtasks] = useState<Set<string>>(new Set())
    const [toolUsageBySubtask, setToolUsageBySubtask] = useState<Map<string, ToolUsage[]>>(new Map())
    const [agentMessagesBySubtask, setAgentMessagesBySubtask] = useState<Map<string, AgentMessage[]>>(new Map())

    const [maestroExpanded, setMaestroExpanded] = useState(false)
    const [maestroMessage, setMaestroMessage] = useState<{
        phase: string
        message: string
        timestamp: string
    } | null>(null)

    const subtasks = useMemo(() => mergeSubtasks(baseSubtasks, messages), [baseSubtasks, messages])
    const isPlanning = task?.status === 'planning' && subtasks.length === 0

    const loadArtifact = useCallback(async (tid: string, contextKeys: string[]) => {
        const targetKey = contextKeys.find(k => k === 'final_manifest') ?? contextKeys[contextKeys.length - 1]
        try {
            const raw = await api.getContextValue(tid, targetKey)
            try {
                let str = typeof raw === 'string' ? raw : JSON.stringify(raw)
                // Strip markdown code block formatting if present
                str = str.replace(/^```(?:json)?\s*\n?/, '').replace(/\n?```\s*$/, '').trim()
                let parsed = JSON.parse(str)
                // If summary contains a nested JSON code block, extract the real manifest
                if (parsed.summary && typeof parsed.summary === 'string' && parsed.summary.includes('```json')) {
                    const inner = parsed.summary.replace(/```json\s*\n?/, '').replace(/\n?```\s*$/, '').trim()
                    try {
                        parsed = JSON.parse(inner)
                    } catch {
                        // inner parse failed, use outer parsed result
                    }
                }
                setArtifact(parsed as ArtifactManifest)
            } catch {
                const fallback = typeof raw === 'string' ? raw : JSON.stringify(raw)
                setArtifact({ summary: fallback.replace(/^```(?:json)?\s*\n?/, '').replace(/\n?```\s*$/, '').trim(), artifacts: [] })
            }
            setTimeout(() => setPhase('done'), 150)
        } catch (e) {
            console.error('Failed to load artifact:', e)
            setPhase('done')
        }
    }, [])

    // Reset + fetch on taskId change
    useEffect(() => {
        if (!taskId) {
            setTask(null)
            setBaseSubtasks([])
            setArtifact(null)
            setPhase('idle')
            setAgentsExpanded(false)
            setMaestroMessage(null)
            setMaestroExpanded(false)
            setLoading(false)
            prevTaskId.current = null
            processedCountRef.current = 0
            return
        }

        if (prevTaskId.current !== taskId) {
            setPhase('idle')
            setArtifact(null)
            setAgentsExpanded(false)
            prevTaskId.current = taskId
            processedCountRef.current = 0
        }

        setLoading(true)
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
            .finally(() => {
                setLoading(false)
            })
    }, [taskId, loadArtifact])


    // React to WS messages (only process NEW messages)
    useEffect(() => {
        if (!taskId || messages.length === 0) return

        const newMessages = messages.slice(processedCountRef.current)
        processedCountRef.current = messages.length

        if (newMessages.length === 0) return
        const latest = newMessages[newMessages.length - 1]

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
    }, [messages, taskId, phase, loadArtifact])

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

        let latestMaestro = null
        for (let i = messages.length - 1; i >= 0; i--) {
            const msg = messages[i]
            if (msg.type === 'agent_message' && msg.agent_id === 'maestro' && !msg.subtask_id) {
                latestMaestro = {
                    phase: msg.phase,
                    message: msg.message,
                    timestamp: msg.timestamp
                }
                break
            }
        }
        setMaestroMessage(latestMaestro)

        setToolUsageBySubtask(toolUsage)
        setAgentMessagesBySubtask(agentMsgs)
    }, [messages])

    const toggleSubtask = useCallback((id: string) => {
        setExpandedSubtasks(prev => {
            const next = new Set(prev)
            if (next.has(id)) {
                next.delete(id)
            } else {
                next.add(id)
            }
            return next
        })
    }, [])

    const toggleMaestro = useCallback(() => {
        setMaestroExpanded(prev => !prev)
    }, [])

    const toggleAgents = useCallback(() => {
        setAgentsExpanded(prev => !prev)
    }, [])

    return {
        task,
        subtasks,
        artifact,
        phase,
        isPlanning,
        loading,
        expandedSubtasks,
        toolUsageBySubtask,
        agentMessagesBySubtask,
        maestroMessage,
        maestroExpanded,
        agentsExpanded,
        toggleSubtask,
        toggleMaestro,
        toggleAgents,
    }
}
