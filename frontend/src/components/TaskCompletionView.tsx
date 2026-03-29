import React from 'react'
import AgentCard from './AgentCard'
import { FadeSlideIn } from './FadeSlideIn'
import type { ToolUsage, AgentMessage, ArtifactManifest } from '../types'
import type { SubtaskState } from '../hooks/useTaskStage'

interface TaskCompletionViewProps {
    phase: 'done' | 'failed'
    artifact: ArtifactManifest | null
    goal: string | undefined
    subtasks: SubtaskState[]
    agentsExpanded: boolean
    expandedSubtasks: Set<string>
    toolUsageBySubtask: Map<string, ToolUsage[]>
    agentMessagesBySubtask: Map<string, AgentMessage[]>
    onToggleAgents: () => void
    onToggleSubtask: (id: string) => void
}

const TaskCompletionView: React.FC<TaskCompletionViewProps> = ({
    phase,
    artifact,
    goal,
    subtasks,
    agentsExpanded,
    expandedSubtasks,
    toolUsageBySubtask,
    agentMessagesBySubtask,
    onToggleAgents,
    onToggleSubtask,
}) => {
    return (
        <div
            className="transition-all duration-500 ease-out opacity-100 translate-y-0"
        >
            {phase === 'done' && artifact ? (
                <div className="border-b border-white/10">
                    <FadeSlideIn delay={0}>
                        <div className="px-6 py-4 flex items-center gap-3 border-b border-green-500/20 bg-green-500/5">
                            <div className="w-2 h-2 rounded-full bg-green-500 shadow-[0_0_8px_rgba(34,197,94,0.8)]" />
                            <span className="text-sm font-medium text-green-400">Complete</span>
                            <span className="text-sm text-gray-500 ml-auto truncate max-w-xs">
                                {goal}
                            </span>
                        </div>
                    </FadeSlideIn>

                    <FadeSlideIn delay={80}>
                        <div className="px-6 py-6 space-y-6">
                            <p className="text-gray-300 leading-relaxed whitespace-pre-wrap">
                                {artifact.summary}
                            </p>
                            {artifact.artifacts.length > 0 && (
                                <div className="flex flex-wrap gap-2">
                                    {artifact.artifacts.map((a, i) => (
                                        <a
                                            key={i}
                                            href={a.path}
                                            className="inline-flex items-center gap-2 px-4 py-2 rounded-lg bg-white/5 border border-white/10 text-sm text-blue-400 hover:text-blue-300 hover:bg-white/10 transition-colors"
                                        >
                                            {a.label}
                                        </a>
                                    ))}
                                </div>
                            )}
                        </div>
                    </FadeSlideIn>
                </div>
            ) : phase === 'failed' ? (
                <FadeSlideIn>
                    <div className="px-6 py-4 border-b border-red-500/20 bg-red-500/5 flex items-center gap-3">
                        <div className="w-2 h-2 rounded-full bg-red-500" />
                        <span className="text-sm font-medium text-red-400">Task failed</span>
                    </div>
                </FadeSlideIn>
            ) : null}

            {/* Collapsible agent cards */}
            {(phase === 'done' || phase === 'failed') && subtasks.length > 0 && (
                <FadeSlideIn delay={160}>
                    <div className="border-b border-white/[0.06]">
                        <button
                            className="w-full px-6 py-3 flex items-center gap-2 text-left hover:bg-white/[0.02] transition-colors"
                            onClick={onToggleAgents}
                        >
                            <span className={`text-gray-600 text-xs transition-transform duration-200 ${agentsExpanded ? 'rotate-90' : ''}`}>
                                ▶
                            </span>
                            <span className="text-xs text-gray-600 hover:text-gray-400 transition-colors">
                                How this was made
                            </span>
                        </button>

                        <div className={`grid transition-all duration-300 ease-in-out ${agentsExpanded ? 'grid-rows-[1fr] opacity-100' : 'grid-rows-[0fr] opacity-0'
                            }`}>
                            <div className="overflow-hidden">
                                <div className="px-6 pb-4 flex flex-col gap-3">
                                    {[...subtasks]
                                        .sort((a, b) => a.position - b.position)
                                        .map((subtask, i) => (
                                            <FadeSlideIn key={subtask.id} delay={agentsExpanded ? i * 60 : 0}>
                                                <AgentCard
                                                    agentId={subtask.agentId}
                                                    status={subtask.status}
                                                    outputPreview={subtask.outputPreview}
                                                    animationDelay={0}
                                                    toolUsage={toolUsageBySubtask.get(subtask.id)}
                                                    agentMessages={agentMessagesBySubtask.get(subtask.id)}
                                                    isExpanded={expandedSubtasks.has(subtask.id)}
                                                    subtaskName={subtask.name}
                                                    subtaskDescription={subtask.description}
                                                    onToggleExpand={() => onToggleSubtask(subtask.id)}
                                                />
                                            </FadeSlideIn>
                                        ))
                                    }
                                </div>
                            </div>
                        </div>
                    </div>
                </FadeSlideIn>
            )}
        </div>
    )
}

export default TaskCompletionView
