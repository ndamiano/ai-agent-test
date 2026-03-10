import React, { useState } from 'react'
import { api } from '../api/client'
import type { Task } from '../types'
import { useTasks } from '../hooks/useTasks'

interface TaskListPanelProps {
    selectedTaskId: string | null
    onSelectTask: (id: string) => void
}

const TaskListPanel: React.FC<TaskListPanelProps> = ({
    selectedTaskId,
    onSelectTask,
}) => {
    const { tasks, loading, error, refresh } = useTasks()
    const [newGoal, setNewGoal] = useState('')
    const [executionMode, setExecutionMode] = useState('Sequential')
    const [isCreating, setIsCreating] = useState(false)

    const handleCreateTask = async () => {
        if (!newGoal.trim()) return

        setIsCreating(true)
        try {
            const newTask = await api.createTask(newGoal.trim(), executionMode)
            await refresh()
            onSelectTask(newTask.id)
            setNewGoal('')
        } catch (e) {
            console.error('Failed to create task:', e)
        } finally {
            setIsCreating(false)
        }
    }

    const handleKeyDown = (e: React.KeyboardEvent) => {
        if ((e.ctrlKey || e.metaKey) && e.key === 'Enter') {
            e.preventDefault()
            handleCreateTask()
        }
    }

    const getStatusColor = (status: Task['status']) => {
        switch (status) {
            case 'pending':
            case 'planning':
                return 'bg-gray-200 dark:bg-gray-700'
            case 'in_progress':
                return 'bg-yellow-200 dark:bg-yellow-700'
            case 'completed':
                return 'bg-green-200 dark:bg-green-700'
            case 'failed':
                return 'bg-red-200 dark:bg-red-700'
            default:
                return 'bg-gray-200 dark:bg-gray-700'
        }
    }

    const getRelativeTime = (createdAt: string) => {
        const date = new Date(createdAt)
        const now = new Date()
        const diffMs = now.getTime() - date.getTime()
        const diffMins = Math.floor(diffMs / 60000)
        const diffHours = Math.floor(diffMins / 60)
        const diffDays = Math.floor(diffHours / 24)

        if (diffMins < 1) return 'just now'
        if (diffMins < 60) return `${diffMins} minute${diffMins === 1 ? '' : 's'} ago`
        if (diffHours < 24) return `${diffHours} hour${diffHours === 1 ? '' : 's'} ago`
        if (diffDays < 7) return `${diffDays} day${diffDays === 1 ? '' : 's'} ago`
        return date.toLocaleDateString()
    }

    if (loading) {
        return (
            <div className="h-full flex flex-col p-4">
                <h3 className="text-sm font-semibold text-gray-700 dark:text-gray-300 mb-4">
                    Task List
                </h3>
                <div className="flex-1 bg-gray-200 dark:bg-gray-700 rounded p-4 text-gray-600 dark:text-gray-400">
                    <div className="space-y-2">
                        <div className="animate-pulse">
                            <div className="h-4 bg-gray-400 rounded w-3/4"></div>
                        </div>
                        <div className="animate-pulse">
                            <div className="h-4 bg-gray-400 rounded w-2/3"></div>
                        </div>
                        <div className="animate-pulse">
                            <div className="h-4 bg-gray-400 rounded w-1/2"></div>
                        </div>
                    </div>
                </div>
            </div>
        )
    }

    if (error) {
        return (
            <div className="h-full flex flex-col p-4">
                <h3 className="text-sm font-semibold text-gray-700 dark:text-gray-300 mb-4">
                    Task List
                </h3>
                <div className="flex-1 bg-red-50 dark:bg-red-900/50 rounded p-4 text-red-600 dark:text-red-400">
                    <p>Error loading tasks: {error}</p>
                </div>
            </div>
        )
    }

    return (
        <div className="h-full flex flex-col p-4">
            <h3 className="text-sm font-semibold text-gray-700 dark:text-gray-300 mb-4">
                Task List
            </h3>

            {/* New Task Input */}
            <div className="mb-4">
                <div className="bg-gray-50 dark:bg-gray-800/50 rounded-lg p-4 mb-3">
                    <div className="flex items-start gap-3">
                        <textarea
                            className="flex-1 bg-white dark:bg-gray-800 border border-gray-300 dark:border-gray-600 rounded-lg p-3 text-sm resize-none min-h-[60px]"
                            placeholder="Enter task goal..."
                            value={newGoal}
                            onChange={(e) => setNewGoal(e.target.value)}
                            onKeyDown={handleKeyDown}
                            rows={1}
                            style={{ lineHeight: '1.5' }}
                        />
                        <div className="flex flex-col gap-2">
                            <select
                                className="px-3 py-2 bg-white dark:bg-gray-800 border border-gray-300 dark:border-gray-600 rounded-lg text-sm"
                                value={executionMode}
                                onChange={(e) => setExecutionMode(e.target.value)}
                            >
                                <option value="Sequential">Sequential</option>
                                <option value="Parallel">Parallel</option>
                            </select>
                            <button
                                onClick={handleCreateTask}
                                disabled={isCreating || !newGoal.trim()}
                                className="px-4 py-2 bg-blue-500 dark:bg-blue-600 text-white text-sm font-medium rounded-lg hover:bg-blue-600 dark:hover:bg-blue-700 disabled:bg-gray-400 dark:disabled:bg-gray-600 disabled:cursor-not-allowed disabled:opacity-50 transition-colors"
                            >
                                {isCreating ? 'Creating...' : 'Submit'}
                            </button>
                        </div>
                    </div>
                </div>
            </div>

            {/* Task List */}
            <div className="flex-1 bg-gray-200 dark:bg-gray-700 rounded p-4 text-gray-600 dark:text-gray-400 overflow-y-auto">
                {tasks.length === 0 ? (
                    <div className="text-center text-gray-500 dark:text-gray-400 py-8">
                        <p className="text-sm mb-2">No tasks yet — create one above</p>
                    </div>
                ) : (
                    <div className="space-y-2">
                        {tasks.map((task) => (
                            <div
                                key={task.id}
                                onClick={() => onSelectTask(task.id)}
                                className={`group cursor-pointer p-3 rounded-lg hover:bg-gray-100 dark:hover:bg-gray-800 transition-colors ${selectedTaskId === task.id
                                    ? 'bg-blue-50 dark:bg-blue-900/30 border-l-4 border-blue-500 dark:border-blue-400 pl-2'
                                    : 'border-l-4 border-transparent pl-2'
                                    }`}
                            >
                                <div className="flex items-center justify-between">
                                    <div className="flex items-center gap-2">
                                        <div
                                            className={`w-2 h-2 rounded-full ${getStatusColor(task.status)}`}
                                        ></div>
                                        <div className="flex-1 min-w-0">
                                            <p className="text-sm font-medium text-gray-900 dark:text-gray-200 group-hover:text-gray-800 dark:group-hover:text-gray-300 truncate">
                                                {task.goal}
                                            </p>
                                            <p className="text-xs text-gray-500 dark:text-gray-400 mt-0.5">
                                                {getRelativeTime(task.created_at)}
                                            </p>
                                        </div>
                                    </div>
                                </div>
                            </div>
                        ))}
                    </div>
                )}
            </div>
        </div>
    )
}

export default TaskListPanel