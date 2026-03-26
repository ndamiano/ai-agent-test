import React from 'react'
import { useTaskStage } from '../hooks/useTaskStage'
import TaskCompletionView from './TaskCompletionView'
import TaskLiveView from './TaskLiveView'

interface StagePanelProps {
    taskId: string | null
}

const StagePanel: React.FC<StagePanelProps> = ({ taskId }) => {
    const {
        task,
        subtasks,
        artifact,
        phase,
        isPlanning,
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

    const liveVisible = phase === 'live' || phase === 'completing'
    const doneVisible = phase === 'done' || phase === 'failed'

    return (
        <div className="h-full flex flex-col overflow-y-auto">
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
                        agentsExpanded={agentsExpanded}
                        expandedSubtasks={expandedSubtasks}
                        toolUsageBySubtask={toolUsageBySubtask}
                        agentMessagesBySubtask={agentMessagesBySubtask}
                        onToggleAgents={toggleAgents}
                        onToggleSubtask={toggleSubtask}
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
