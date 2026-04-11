import React from 'react'
import { useTaskStage } from '../hooks/useTaskStage'
import TaskCompletionView from './TaskCompletionView'
import TaskLiveView from './TaskLiveView'
import RefinementChat from './RefinementChat'

interface StagePanelProps {
    taskId: string | null
}

const StagePanel: React.FC<StagePanelProps> = ({ taskId }) => {
    const {
        task,
        subtasks,
        childTasks,
        artifact,
        criteria,
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
    } = useTaskStage(taskId)

    if (!taskId) {
        return (
            <div className="h-full flex items-center justify-center">
                <p className="text-sm text-gray-600">Create a task to get started</p>
            </div>
        )
    }

    if (loading) {
        return (
            <div className="h-full flex flex-col gap-4 p-4">
                <div className="h-12 rounded-lg bg-white/5 animate-pulse" />
                <div className="h-10 rounded-lg bg-white/5 animate-pulse" />
                <div className="flex-1 rounded-lg bg-white/5 animate-pulse" />
                <div className="flex-1 rounded-lg bg-white/5 animate-pulse" />
                <div className="flex-1 rounded-lg bg-white/5 animate-pulse" />
            </div>
        )
    }

    const refiningVisible = phase === 'refining' || phase === 'synthesizing'
    const liveVisible = phase === 'live' || phase === 'completing'
    const doneVisible = phase === 'done' || phase === 'failed'

    return (
        <div className="h-full flex flex-col overflow-y-auto">
            {/* Refining / Synthesizing view */}
            {refiningVisible && taskId && (
                <RefinementChat
                    taskId={taskId}
                    isSynthesizing={phase === 'synthesizing'}
                    goal={task?.goal ?? ''}
                />
            )}

            {/* Done / Failed view */}
            <div
                className={`transition-all duration-500 ease-out ${doneVisible ? 'opacity-100 translate-y-0' : 'opacity-0 -translate-y-2 pointer-events-none'
                    }`}
                aria-hidden={!doneVisible}
            >
                {(phase === 'done' || phase === 'failed') && (
                    <TaskCompletionView
                        phase={phase === 'failed' ? 'failed' : 'done'}
                        artifact={artifact}
                        goal={task?.goal}
                        subtasks={subtasks}
                        criteria={criteria}
                        agentsExpanded={agentsExpanded}
                        onToggleAgents={toggleAgents}
                    />
                )}
            </div>

            {/* Live / In-progress view */}
            <div
                className={`transition-all duration-400 ease-in-out ${liveVisible ? 'opacity-100 translate-y-0' : 'opacity-0 translate-y-2 pointer-events-none'
                    }`}
                aria-hidden={!liveVisible}
            >
                <TaskLiveView
                    task={task}
                    isPlanning={isPlanning}
                    subtasks={subtasks}
                    childTasks={childTasks}
                    criteria={criteria}
                    maestroMessage={maestroMessage}
                    maestroExpanded={maestroExpanded}
                    expandedSubtasks={expandedSubtasks}
                    toolUsageBySubtask={toolUsageBySubtask}
                    agentMessagesBySubtask={agentMessagesBySubtask}
                    onToggleMaestro={toggleMaestro}
                    onToggleSubtask={toggleSubtask}
                />
            </div>
        </div>
    )
}

export default StagePanel
