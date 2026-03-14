import React from 'react'
import { Trash2 } from 'lucide-react'
import type { Task } from '../types'

interface TaskItemProps {
    task: Task
    isSelected: boolean
    onSelect: (id: string) => void
    onArchive: (id: string) => void
    sectionType: 'needs_assistance' | 'in_progress' | 'completed'
}

const TaskItem: React.FC<TaskItemProps> = ({
    task,
    isSelected,
    onSelect,
    onArchive,
    sectionType
}) => {
    const getRelativeTime = (createdAt: string) => {
        const date = new Date(createdAt)
        const now = new Date()
        const diffMs = now.getTime() - date.getTime()
        const diffMins = Math.floor(diffMs / 60000)
        const diffHours = Math.floor(diffMins / 60)
        const diffDays = Math.floor(diffHours / 24)
        if (diffMins < 1) return 'just now'
        if (diffMins < 60) return `${diffMins}m ago`
        if (diffHours < 24) return `${diffHours}h ago`
        if (diffDays < 7) return `${diffDays}d ago`
        return date.toLocaleDateString()
    }

    const getBorderColor = () => {
        if (sectionType === 'needs_assistance') return 'border-orange-500'
        if (sectionType === 'in_progress') return isSelected ? 'border-blue-500' : 'border-transparent'
        if (task.status === 'failed') return 'border-red-500'
        return 'border-green-500'
    }

    const getSelectedBg = () => {
        if (!isSelected) return ''
        if (sectionType === 'needs_assistance') return 'bg-orange-900/20'
        if (sectionType === 'in_progress') return 'bg-blue-900/20'
        return task.status === 'failed' ? 'bg-red-900/20' : 'bg-green-900/20'
    }

    const getDotColor = () => {
        if (sectionType === 'needs_assistance') return 'bg-orange-500'
        if (sectionType === 'in_progress') return 'bg-yellow-500'
        return task.status === 'failed' ? 'bg-red-500' : 'bg-green-500'
    }

    return (
        <div
            className={`group cursor-pointer flex items-center gap-2 px-2 py-2.5 rounded-lg border-l-4 transition-colors hover:bg-white/5 ${getBorderColor()} ${getSelectedBg()}`}
            onClick={() => onSelect(task.id)}
        >
            <div className={`w-1.5 h-1.5 rounded-full flex-shrink-0 ${getDotColor()} ${sectionType === 'in_progress' ? 'animate-pulse' : ''}`} />

            <div className="flex-1 min-w-0">
                <p className="text-sm text-gray-200 truncate">{task.goal}</p>
                <p className="text-xs text-gray-500 mt-0.5">{getRelativeTime(task.created_at)}</p>
            </div>

            <button
                className="flex-shrink-0 opacity-0 group-hover:opacity-100 transition-opacity p-1 rounded text-gray-600 hover:text-red-400 hover:bg-white/10"
                onClick={(e) => {
                    e.stopPropagation()
                    onArchive(task.id)
                }}
                title="Archive"
            >
                <Trash2 size={14} />
            </button>
        </div>
    )
}

export default TaskItem