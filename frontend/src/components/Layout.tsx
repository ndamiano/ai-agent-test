import React from 'react'
import TaskListPanel from './TaskListPanel'
import StagePanel from './StagePanel'
import CommandBar from './CommandBar'

interface LayoutProps {
    selectedTaskId: string | null
    setSelectedTaskId: (id: string | null) => void
    systemStatus: { connected: boolean; message: string }
    onTaskCreate: (goal: string) => void
}

const Layout: React.FC<LayoutProps> = ({
    selectedTaskId,
    setSelectedTaskId,
    systemStatus,
    onTaskCreate,
}) => {
    return (
        <div className="h-screen flex flex-col overflow-hidden bg-[#0f0f0f]">

            {/* Header */}
            <div className="flex-shrink-0 border-b border-white/[0.06] px-4 py-2 flex items-center justify-between bg-[#0f0f0f]">
                <div className="text-white font-semibold text-sm tracking-wide">
                    AI Agent
                </div>
                <div className="flex items-center gap-2">
                    <div className={`w-1.5 h-1.5 rounded-full ${systemStatus.connected ? 'bg-green-500' : 'bg-red-500'}`} />
                    <span className="text-xs text-gray-600">{systemStatus.message}</span>
                </div>
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