import React from 'react'
import AgentCard from './AgentCard'
import MaestroCard from './MaestroCard'
import BounceDots from './BounceDots'
import type { ToolUsage, AgentMessage } from '../types'
import type { SubtaskState } from '../hooks/useTaskStage'

interface TaskLiveViewProps {
    task: { goal: string } | null
    isPlanning: boolean
    subtasks: SubtaskState[]
    maestroMessage: { phase: string; message: string; timestamp: string } | null
    maestroExpanded: boolean
    expandedSubtasks: Set<string>
    toolUsageBySubtask: Map<string, ToolUsage[]>
    agentMessagesBySubtask: Map<string, AgentMessage[]>
    onToggleMaestro: () => void
    onToggleSubtask: (id: string) => void
}

const TaskLiveView: React.FC<TaskLiveViewProps> = ({
    task,
    isPlanning,
    subtasks,
    maestroMessage,
    maestroExpanded,
    expandedSubtasks,
    toolUsageBySubtask,
    agentMessagesBySubtask,
    onToggleMaestro,
    onToggleSubtask,
}) => {
    return (
        <div className="flex flex-col gap-5 p-6">
            {task && (
                <div>
                    <p className="text-white font-medium leading-snug mb-3">{task.goal}</p>
                </div>
            )}

            {isPlanning && (
                <div className="flex items-center gap-3 py-2">
                    <BounceDots />
                    <span className="text-sm text-gray-500">Planning...</span>
                </div>
            )}

            {maestroMessage && (
                <div className="mb-4">
                    <MaestroCard
                        phase={maestroMessage.phase as any}
                        message={maestroMessage.message}
                        timestamp={maestroMessage.timestamp}
                        isExpanded={maestroExpanded}
                        onToggleExpand={onToggleMaestro}
                    />
                </div>
            )}

            {subtasks.length > 0 && (
                <div className="flex flex-col gap-3">
                    {[...subtasks]
                        .sort((a, b) => a.position - b.position)
                        .map(subtask => (
                            <AgentCard
                                key={subtask.id}
                                agentId={subtask.agentId}
                                status={subtask.status}
                                outputPreview={subtask.outputPreview}
                                animationDelay={subtask.position * 80}
                                toolUsage={toolUsageBySubtask.get(subtask.id)}
                                agentMessages={agentMessagesBySubtask.get(subtask.id)}
                                isExpanded={expandedSubtasks.has(subtask.id)}
                                subtaskName={subtask.name}
                                subtaskDescription={subtask.description}
                                onToggleExpand={() => onToggleSubtask(subtask.id)}
                            />
                        ))
                    }
                </div>
            )}
        </div>
    )
}

export default TaskLiveView
