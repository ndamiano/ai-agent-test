import React, { useState, useEffect } from 'react'
import { useTaskSocket } from '../hooks/useTaskSocket'
import { api } from '../api/client'
import type { TaskDetail, Subtask } from '../types'
import AgentCard from './AgentCard'

interface ActivityFeedPanelProps {
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
    console.log('merging', base.length, 'subtasks with', messages.length, 'messages')
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

const ActivityFeedPanel: React.FC<ActivityFeedPanelProps> = ({ taskId }) => {
    const { messages } = useTaskSocket(taskId)
    console.log('render', messages.length) // add this temporarily
    const [task, setTask] = useState<TaskDetail | null>(null)
    const [baseSubtasks, setBaseSubtasks] = useState<SubtaskState[]>([])

    const subtasks = mergeSubtasks(baseSubtasks, messages)

    // Fetch task on mount / taskId change
    useEffect(() => {
        if (!taskId) {
            setTask(null)
            setBaseSubtasks([])
            return
        }
        api.getTask(taskId)
            .then(detail => {
                setTask(detail)
                setBaseSubtasks(detail.subtasks.map(toSubtaskState))
            })
            .catch(() => {
                setTask(null)
                setBaseSubtasks([])
            })
    }, [taskId])

    useEffect(() => {
        const latest = messages[messages.length - 1]
        if (!latest) return

        if (
            latest.type === 'task_completed' ||
            latest.type === 'task_failed' ||
            latest.type === 'task_status' ||
            latest.type === 'subtask_started'  // first subtask_started means planning is done
        ) {
            if (taskId) {
                api.getTask(taskId)
                    .then(detail => {
                        setTask(detail)
                        if (detail.subtasks.length > 0) {
                            setBaseSubtasks(detail.subtasks.map(toSubtaskState))
                        }
                    })
                    .catch(console.error)
            }
        }
    }, [messages, taskId])

    // Empty state
    if (!taskId) {
        return (
            <div className="h-full flex items-center justify-center">
                <p className="text-sm text-gray-600">Select a task to see activity</p>
            </div>
        )
    }

    const { label, color } = statusLabel(task?.status ?? 'pending')
    const isLive = task?.status === 'in_progress' || task?.status === 'planning'
    const isPlanning = task?.status === 'planning' && subtasks.length === 0

    return (
        <div className="h-full flex flex-col gap-5 p-5 overflow-y-auto">

            {/* Task header */}
            {task && (
                <div>
                    <p className="text-white font-medium leading-snug mb-3">{task.goal}</p>
                    <div className="flex items-center gap-2">
                        {isLive && (
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

            {/* Agent cards */}
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
                            />
                        ))
                    }
                </div>
            )}

        </div>
    )
}

export default ActivityFeedPanel