import React from 'react'
import ReactMarkdown from 'react-markdown'
import AgentCard from './AgentCard'
import { FadeSlideIn } from './FadeSlideIn'
import type { ToolUsage, AgentMessage } from '../types'

interface SubtaskState {
    id: string
    agentId: string
    status: 'pending' | 'in_progress' | 'completed' | 'failed'
    outputPreview: string | null
    position: number
    name: string | null
    description: string | null
}

interface TaskCompletionViewProps {
    phase: 'done' | 'failed'
    artifact: string | null
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
            className={`transition-all duration-500 ease-out opacity-100 translate-y-0`}
            aria-hidden={false}
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
                        <div className="px-6 py-6 [&_*]:!text-inherit text-gray-200
                            [&_h1]:text-white [&_h1]:font-semibold [&_h1]:text-xl [&_h1]:mb-3 [&_h1]:mt-5
                            [&_h2]:text-white [&_h2]:font-semibold [&_h2]:text-lg [&_h2]:mb-2 [&_h2]:mt-4
                            [&_h3]:text-white [&_h3]:font-medium [&_h3]:text-base [&_h3]:mb-2 [&_h3]:mt-3
                            [&_p]:text-gray-300 [&_p]:leading-relaxed [&_p]:mb-3
                            [&_strong]:text-white [&_strong]:font-semibold
                            [&_em]:text-gray-300 [&_em]:italic
                            [&_ul]:text-gray-300 [&_ul]:pl-5 [&_ul]:mb-3 [&_ul]:list-disc
                            [&_ol]:text-gray-300 [&_ol]:pl-5 [&_ol]:mb-3 [&_ol]:list-decimal
                            [&_li]:text-gray-300 [&_li]:mb-1
                            [&_li::marker]:text-gray-500
                            [&_blockquote]:border-l-2 [&_blockquote]:border-gray-600 [&_blockquote]:pl-4 [&_blockquote]:text-gray-400 [&_blockquote]:italic [&_blockquote]:my-3
                            [&_code]:text-blue-300 [&_code]:bg-white/5 [&_code]:px-1.5 [&_code]:py-0.5 [&_code]:rounded [&_code]:text-sm
                            [&_pre]:bg-white/5 [&_pre]:rounded-lg [&_pre]:p-4 [&_pre]:mb-3 [&_pre]:overflow-x-auto
                            [&_pre_code]:bg-transparent [&_pre_code]:p-0 [&_pre_code]:text-blue-300
                            [&_hr]:border-white/10 [&_hr]:my-4
                            [&_a]:text-blue-400 [&_a]:underline [&_a:hover]:text-blue-300
                            [&_table]:w-full [&_table]:text-sm [&_table]:mb-3
                            [&_th]:text-gray-200 [&_th]:font-semibold [&_th]:text-left [&_th]:pb-2 [&_th]:border-b [&_th]:border-white/10
                            [&_td]:text-gray-300 [&_td]:py-2 [&_td]:border-b [&_td]:border-white/5
                        ">
                            <ReactMarkdown>{artifact}</ReactMarkdown>
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
