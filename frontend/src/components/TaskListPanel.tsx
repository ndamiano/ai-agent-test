import React from 'react'
import { api } from '../api/client'
import type { Task } from '../types'
import TaskItem from './TaskItem'

interface TaskListPanelProps {
    selectedTaskId: string | null
    onSelectTask: (id: string | null) => void
    tasks: Task[]
    loading: boolean
    error: string | null
    onRefresh: () => Promise<void>
}

const TaskListPanel: React.FC<TaskListPanelProps> = ({ selectedTaskId, onSelectTask, tasks, loading, error, onRefresh }) => {

    const handleArchiveTask = async (taskId: string) => {
        try {
            await api.deleteTask(taskId)
            await onRefresh()
            if (selectedTaskId === taskId) onSelectTask(null)
        } catch (e) {
            console.error('Failed to archive task:', e)
        }
    }

    const sortByNewest = (taskList: Task[]) =>
        [...taskList].sort((a, b) => new Date(b.created_at).getTime() - new Date(a.created_at).getTime())

    const inProgress = sortByNewest(tasks.filter(t => t.status === 'pending' || t.status === 'planning' || t.status === 'in_progress'))
    const completed = sortByNewest(tasks.filter(t => t.status === 'completed' || t.status === 'failed'))

    if (loading) return (
        <div className="h-full flex flex-col gap-4 p-4">
            {[...Array(3)].map((_, i) => (
                <div key={i} className="flex-1 rounded-lg bg-white/5 animate-pulse" />
            ))}
        </div>
    )

    if (error) return (
        <div className="h-full flex items-center justify-center p-4">
            <p className="text-sm text-red-400">Failed to load tasks</p>
        </div>
    )

    return (
        <div className="h-full flex flex-col gap-3 p-3 overflow-hidden">

            {/* In Progress — always shown */}
            <Section
                title="In Progress"
                titleClass="text-gray-400"
                trailing={inProgress.length === 0
                    ? <span className="text-xs text-gray-600">None running</span>
                    : undefined
                }
            >
                {inProgress.length === 0
                    ? <Empty message="No tasks in progress" />
                    : inProgress.map(task => (
                        <TaskItem
                            key={task.id}
                            task={task}
                            isSelected={selectedTaskId === task.id}
                            onSelect={onSelectTask}
                            onArchive={handleArchiveTask}
                            sectionType="in_progress"
                        />
                    ))
                }
            </Section>

            {/* Completed — always shown */}
            <Section title="Completed" titleClass="text-gray-400">
                {completed.length === 0
                    ? <Empty message="No completed tasks yet" />
                    : completed.map(task => (
                        <TaskItem
                            key={task.id}
                            task={task}
                            isSelected={selectedTaskId === task.id}
                            onSelect={onSelectTask}
                            onArchive={handleArchiveTask}
                            sectionType="completed"
                        />
                    ))
                }
            </Section>

        </div>
    )
}

// -- Helpers --

interface SectionProps {
    title: string
    titleClass?: string
    dot?: string
    trailing?: React.ReactNode
    children: React.ReactNode
}

const Section: React.FC<SectionProps> = ({ title, titleClass = '', dot, trailing, children }) => (
    <div className="flex-1 flex flex-col min-h-0 rounded-lg bg-white/[0.03] border border-white/[0.06] p-3">
        <div className="flex items-center justify-between mb-2 flex-shrink-0">
            <div className="flex items-center gap-2">
                {dot && <div className={`w-1.5 h-1.5 rounded-full ${dot}`} />}
                <h4 className={`text-xs font-semibold uppercase tracking-wider ${titleClass}`}>
                    {title}
                </h4>
            </div>
            {trailing}
        </div>
        <div className="flex-1 overflow-y-auto space-y-1 min-h-0">
            {children}
        </div>
    </div>
)

const Empty: React.FC<{ message: string }> = ({ message }) => (
    <div className="h-full flex items-center justify-center">
        <p className="text-xs text-gray-600">{message}</p>
    </div>
)

export default TaskListPanel