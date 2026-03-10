import React, { useState, useEffect, useRef, useCallback } from 'react'
import { useTaskSocket } from '../hooks/useTaskSocket'
import type { TaskDetail, Subtask } from '../types'

interface ActivityFeedPanelProps {
    taskId: string | null
}

const ActivityFeedPanel: React.FC<ActivityFeedPanelProps> = ({ taskId }) => {
    const { messages, connected, finished } = useTaskSocket(taskId)
    const [task, setTask] = useState<TaskDetail | null>(null)
    const [isScrolling, setIsScrolling] = useState(false)
    const feedRef = useRef<HTMLDivElement>(null)
    const scrollRef = useRef<HTMLDivElement>(null)

    const [taskData, setTaskData] = useState<TaskDetail | null>(null)

    useEffect(() => {
        const taskMessage = messages.find(
            (msg): msg is { type: 'task_status'; data: TaskDetail } => msg.type === 'task_status'
        )
        if (taskMessage) {
            setTaskData(taskMessage.data)
        }
    }, [messages])

    useEffect(() => {
        if (taskData) {
            setTask(taskData)
        }
    }, [taskData])

    const handleScroll = useCallback(() => {
        if (scrollRef.current) {
            setIsScrolling(scrollRef.current.scrollTop > 0)
        }
    }, [])

    useEffect(() => {
        if (scrollRef.current) {
            scrollRef.current.addEventListener('scroll', handleScroll)
        }
        return () => {
            if (scrollRef.current) {
                scrollRef.current.removeEventListener('scroll', handleScroll)
            }
        }
    }, [handleScroll])

    useEffect(() => {
        if (!isScrolling && feedRef.current && connected && !finished) {
            feedRef.current.scrollTop = feedRef.current.scrollHeight
        }
    }, [messages, isScrolling, connected, finished])

    const getEventTypeIcon = (type: string) => {
        const icons: Record<string, string> = {
            subtask_started: '⚡',
            subtask_completed: '✅',
            subtask_failed: '❌',
            agent_message: '💬',
            task_completed: '🎉',
            task_failed: '🚨',
        }
        return icons[type] || '📋'
    }

    const getEventTypeColor = (type: string) => {
        const colors: Record<string, string> = {
            subtask_started: 'bg-yellow-100 border-yellow-400 text-yellow-800',
            subtask_completed: 'bg-green-100 border-green-400 text-green-800',
            subtask_failed: 'bg-red-100 border-red-400 text-red-800',
            agent_message: 'bg-blue-100 border-blue-400 text-blue-800',
            task_completed: 'bg-green-100 border-green-400 text-green-800',
            task_failed: 'bg-red-100 border-red-400 text-red-800',
        }
        return colors[type] || 'bg-gray-100 border-gray-400 text-gray-800'
    }

    const renderSubtask = (subtask: Subtask, index: number) => {
        const isRoot = subtask.depends_on.length === 0
        const marginLeft = isRoot ? 0 : 24

        return (
            <div
                key={subtask.id}
                className={`flex items-start gap-3 ${isRoot ? '' : 'ml-4'}`}
                style={{ marginLeft }}
            >
                <div className="flex-shrink-0 w-8 text-center">
                    <div className="text-xs font-medium text-gray-500 dark:text-gray-400">
                        {subtask.position}
                    </div>
                </div>
                <div className="flex-1 min-w-0">
                    <div className="flex items-center gap-2">
                        <div
                            className={`px-2 py-1 rounded-full text-xs font-medium ${subtask.status === 'completed'
                                ? 'bg-green-100 text-green-800'
                                : subtask.status === 'failed'
                                    ? 'bg-red-100 text-red-800'
                                    : subtask.status === 'in_progress'
                                        ? 'bg-yellow-100 text-yellow-800'
                                        : 'bg-gray-100 text-gray-800'
                                }`}
                        >
                            {subtask.agent_id}
                        </div>
                        <span className="text-sm font-medium text-gray-900 dark:text-white">
                            {subtask.goal}
                        </span>
                    </div>
                    {subtask.output_preview && (
                        <div className="mt-2 p-2 bg-gray-50 dark:bg-gray-800 rounded text-sm text-gray-600 dark:text-gray-300">
                            {subtask.output_preview}
                        </div>
                    )}
                </div>
            </div>
        )
    }

    if (!taskId) {
        return (
            <div className="h-full flex flex-col p-4">
                <h3 className="text-sm font-semibold text-gray-700 dark:text-gray-300 mb-4">
                    Activity Feed
                </h3>
                <div className="flex-1 bg-gray-200 dark:bg-gray-700 rounded p-4 text-gray-600 dark:text-gray-400 flex items-center justify-center">
                    <div className="text-center">
                        <div className="text-4xl mb-4 text-gray-400 dark:text-gray-600">🔍</div>
                        <h4 className="text-lg font-medium text-gray-900 dark:text-white mb-2">
                            Select a task to see activity
                        </h4>
                        <p className="text-gray-600 dark:text-gray-300">
                            Choose a task from the list to view its live activity feed
                        </p>
                    </div>
                </div>
            </div>
        )
    }

    return (
        <div className="h-full flex flex-col p-4">
            <div className="flex items-center justify-between mb-4">
                <div>
                    <h3 className="text-sm font-semibold text-gray-700 dark:text-gray-300">
                        Activity Feed
                    </h3>
                    {task && (
                        <div className="flex items-center gap-2 mt-1">
                            <div className="flex items-center gap-1">
                                <div className="w-2 h-2 bg-green-500 rounded-full animate-pulse" />
                                <span className="text-xs text-gray-500 dark:text-gray-400">
                                    {task.status === 'in_progress' ? 'In Progress' : 'Idle'}
                                </span>
                            </div>
                            <div className="flex items-center gap-1">
                                <span className="text-xs text-gray-500 dark:text-gray-400">
                                    {task.execution_mode}
                                </span>
                            </div>
                        </div>
                    )}
                </div>
                {connected && (
                    <div className="flex items-center gap-2 text-xs text-gray-500 dark:text-gray-400">
                        <div className="flex items-center gap-1">
                            <div className="w-2 h-2 bg-green-500 rounded-full" />
                            <span>Connected</span>
                        </div>
                    </div>
                )}
            </div>

            {task && (
                <div className="mb-4">
                    <div className="bg-white dark:bg-gray-800 rounded-lg shadow-sm border border-gray-200 dark:border-gray-700 p-4 mb-3">
                        <h4 className="text-sm font-semibold text-gray-900 dark:text-white mb-2">
                            Task
                        </h4>
                        <div className="text-gray-900 dark:text-white text-sm leading-relaxed">
                            {task.goal}
                        </div>
                    </div>

                    {task.subtasks.length > 0 && (
                        <div className="bg-white dark:bg-gray-800 rounded-lg shadow-sm border border-gray-200 dark:border-gray-700 p-4 mb-4">
                            <h4 className="text-sm font-semibold text-gray-900 dark:text-white mb-3">
                                Plan
                            </h4>
                            <div className="space-y-3">
                                {task.subtasks
                                    .sort((a, b) => a.position - b.position)
                                    .map((subtask, index) => (
                                        <div key={subtask.id}>
                                            {renderSubtask(subtask, index)}
                                        </div>
                                    ))}
                            </div>
                        </div>
                    )}
                </div>
            )}

            <div
                ref={scrollRef}
                className="flex-1 bg-white dark:bg-gray-800 rounded-lg shadow-sm border border-gray-200 dark:border-gray-700 overflow-hidden"
            >
                <div
                    ref={feedRef}
                    className="h-full overflow-y-auto p-4 space-y-3"
                    onScroll={handleScroll}
                >
                    {messages.length === 0 ? (
                        <div className="text-center py-8 text-gray-500 dark:text-gray-400">
                            <div className="text-4xl mb-2">📋</div>
                            <p>No activity yet</p>
                            <p className="text-sm">Events will appear here as they happen</p>
                        </div>
                    ) : (
                        messages.map((message, index) => {
                            if (message.type === 'task_status') return null

                            const timestamp = new Date().toISOString()
                            const isLast = index === messages.length - 1

                            return (
                                <div
                                    key={message.type + timestamp}
                                    className={`flex items-start gap-3 p-3 rounded-lg ${isLast && connected && !finished
                                        ? 'animate-pulse'
                                        : 'not-animate'
                                        }`}
                                >
                                    <div className="flex-shrink-0 text-center">
                                        <div className="text-lg font-medium">
                                            {getEventTypeIcon(message.type)}
                                        </div>
                                        <div className="text-xs text-gray-400 dark:text-gray-600">
                                            {new Date().toLocaleTimeString([], {
                                                hour: '2-digit',
                                                minute: '2-digit',
                                            })}
                                        </div>
                                    </div>
                                    <div className="flex-1 min-w-0">
                                        <div
                                            className={`flex items-center gap-2 mb-1 ${getEventTypeColor(
                                                message.type
                                            )}`}
                                        >
                                            <span className="text-xs font-medium">
                                                {message.type.replace('_', ' ')}
                                            </span>
                                        </div>
                                        <div className="text-sm text-gray-900 dark:text-white">
                                            {message.type === 'agent_message'
                                                ? message.message
                                                : message.type.includes('subtask')
                                                    ? 'Subtask event'
                                                    : 'Task event'}
                                        </div>
                                    </div>
                                </div>
                            )
                        })
                    )}
                </div>
            </div>
        </div>
    )
};

export default ActivityFeedPanel;