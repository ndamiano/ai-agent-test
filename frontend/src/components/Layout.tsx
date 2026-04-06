import React from 'react'
import TaskListPanel from './TaskListPanel'
import StagePanel from './StagePanel'
import CommandBar from './CommandBar'
import type { Task } from '../types'

interface LayoutProps {
    selectedTaskId: string | null
    setSelectedTaskId: (id: string | null) => void
    onTaskCreate: (goal: string) => void
    onSettingsClick: () => void
    tasks: Task[]
    tasksLoading: boolean
    tasksError: string | null
    onRefreshTasks: () => Promise<void>
}

const Layout: React.FC<LayoutProps> = ({
    selectedTaskId,
    setSelectedTaskId,
    onTaskCreate,
    onSettingsClick,
    tasks,
    tasksLoading,
    tasksError,
    onRefreshTasks,
}) => {
    return (
        <div className="h-screen flex flex-col overflow-hidden bg-[#0f0f0f]">

            {/* Header */}
            <div className="flex-shrink-0 border-b border-white/[0.06] px-4 py-2 flex items-center justify-between bg-[#0f0f0f]">
                <div className="text-white font-semibold text-sm tracking-wide">
                    AI Agent
                </div>
                <button
                    onClick={onSettingsClick}
                    className="text-gray-400 hover:text-white transition-colors p-1"
                    title="Settings"
                >
                    <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M10.325 4.317c.426-1.756 2.924-1.756 3.35 0a1.724 1.724 0 002.573 1.066c1.543-.94 3.31.826 2.37 2.37a1.724 1.724 0 001.065 2.572c1.756.426 1.756 2.924 0 3.35a1.724 1.724 0 00-1.066 2.573c.94 1.543-.826 3.31-2.37 2.37a1.724 1.724 0 00-2.572 1.065c-.426 1.756-2.924 1.756-3.35 0a1.724 1.724 0 00-2.573-1.066c-1.543.94-3.31-.826-2.37-2.37a1.724 1.724 0 00-1.065-2.572c-1.756-.426-1.756-2.924 0-3.35a1.724 1.724 0 001.066-2.573c-.94-1.543.826-3.31 2.37-2.37.996.608 2.296.07 2.572-1.065z" />
                        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M15 12a3 3 0 11-6 0 3 3 0 016 0z" />
                    </svg>
                </button>
            </div>

            {/* Command bar */}
            <div className="flex-shrink-0">
                <CommandBar onTaskCreate={onTaskCreate} />
            </div>

            {/* Two panel layout */}
            <div className="flex-1 flex overflow-hidden">

                {/* Left — task list */}
                <div className="w-72 flex-shrink-0 border-r border-white/[0.06]">
                    <TaskListPanel
                        selectedTaskId={selectedTaskId}
                        onSelectTask={setSelectedTaskId}
                        tasks={tasks}
                        loading={tasksLoading}
                        error={tasksError}
                        onRefresh={onRefreshTasks}
                    />
                </div>

                {/* Right — stage */}
                <div className="flex-1 min-w-0">
                    <StagePanel taskId={selectedTaskId} />
                </div>

            </div>
        </div>
    )
}

export default Layout