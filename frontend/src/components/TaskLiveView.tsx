import React from 'react'
import AgentCard from './AgentCard'
import MaestroCard from './MaestroCard'
import BounceDots from './BounceDots'
import type { Task, ToolUsage, AgentMessage, MaestroPhase, PipelineEvent } from '../types'
import type { SubtaskState } from '../hooks/useTaskStage'

interface TaskLiveViewProps {
    task: { goal: string } | null
    isPlanning: boolean
    subtasks: SubtaskState[]
    childTasks: Task[]
    criteria: string[] | null
    pipelineEvents: PipelineEvent[]
    maestroMessage: { phase: MaestroPhase; message: string; timestamp: string } | null
    maestroExpanded: boolean
    expandedSubtasks: Set<string>
    toolUsageBySubtask: Map<string, ToolUsage[]>
    agentMessagesBySubtask: Map<string, AgentMessage[]>
    onToggleMaestro: () => void
    onToggleSubtask: (id: string) => void
}

const statusColors: Record<string, string> = {
    pending: 'bg-gray-600',
    planning: 'bg-yellow-500 animate-pulse',
    in_progress: 'bg-blue-500 animate-pulse',
    completed: 'bg-green-500',
    failed: 'bg-red-500',
}

const statusLabel: Record<string, string> = {
    pending: 'Pending',
    planning: 'Planning',
    in_progress: 'Running',
    completed: 'Done',
    failed: 'Failed',
}

const pipelineEventLabel: Record<PipelineEvent['type'], string> = {
    pipeline_started: 'Started',
    pipeline_node_started: 'Node started',
    pipeline_node_completed: 'Node complete',
    pipeline_node_failed: 'Node failed',
    pipeline_stage_started: 'Stage started',
    pipeline_stage_completed: 'Stage complete',
    pipeline_stage_retrying: 'Retrying',
    pipeline_stage_failed: 'Stage failed',
    pipeline_completed: 'Complete',
    pipeline_failed: 'Failed',
}

interface PipelineRun {
    key: string
    name: string
    workingDir: string
    latest: PipelineEvent
    events: PipelineEvent[]
    completedStages: number
    knownStages: Set<string>
}

function groupPipelineRuns(events: PipelineEvent[]): PipelineRun[] {
    const runs = new Map<string, PipelineRun>()

    for (const event of events) {
        const key = event.working_dir || `${event.pipeline}:${event.subtask_id ?? 'task'}`
        const existing = runs.get(key)
        const run = existing ?? {
            key,
            name: event.pipeline,
            workingDir: event.working_dir,
            latest: event,
            events: [],
            completedStages: 0,
            knownStages: new Set<string>(),
        }

        run.events.push(event)
        run.latest = event
        if (event.stage_id) {
            run.knownStages.add(`${event.node_id ?? 'node'}:${event.stage_id}`)
        }
        if (event.type === 'pipeline_stage_completed') {
            run.completedStages += 1
        }
        runs.set(key, run)
    }

    return [...runs.values()].sort((a, b) => a.events[0].timestamp.localeCompare(b.events[0].timestamp))
}

const PipelineProgress: React.FC<{ events: PipelineEvent[] }> = ({ events }) => {
    const runs = groupPipelineRuns(events)
    if (runs.length === 0) return null

    return (
        <div className="rounded-lg border border-white/[0.06] bg-white/[0.02] px-4 py-3">
            <p className="text-xs font-medium text-gray-500 uppercase tracking-wider mb-3">Pipelines</p>
            <div className="flex flex-col gap-3">
                {runs.map(run => {
                    const latest = run.latest
                    const failed = latest.type === 'pipeline_failed' || latest.type === 'pipeline_node_failed' || latest.type === 'pipeline_stage_failed'
                    const done = latest.type === 'pipeline_completed'
                    const active = !failed && !done
                    const stageTotal = Math.max(run.knownStages.size, latest.stage_count ?? 0)
                    const label = latest.pipeline_path?.join(' / ') ?? run.name
                    const detail = latest.stage_id
                        ? `${latest.node_id ?? 'node'} / ${latest.stage_id}${latest.attempt ? ` (attempt ${latest.attempt}${latest.max_attempts ? `/${latest.max_attempts}` : ''})` : ''}`
                        : latest.node_id ?? latest.working_dir

                    return (
                        <div key={run.key} className="rounded-md bg-white/[0.03] border border-white/[0.05] px-3 py-2">
                            <div className="flex items-center gap-2">
                                <div className={`w-2 h-2 rounded-full flex-shrink-0 ${
                                    failed ? 'bg-red-500' : done ? 'bg-green-500' : 'bg-blue-500 animate-pulse'
                                }`} />
                                <span className="text-sm font-medium text-white">{label}</span>
                                <span className={`text-xs ml-auto ${
                                    failed ? 'text-red-400' : done ? 'text-green-400' : 'text-blue-400'
                                }`}>
                                    {pipelineEventLabel[latest.type]}
                                </span>
                            </div>
                            <div className="mt-1 pl-4 text-xs text-gray-500 truncate">{detail}</div>
                            {stageTotal > 0 && (
                                <div className="mt-2 pl-4">
                                    <div className="h-1.5 rounded-full overflow-hidden bg-white/5">
                                        <div
                                            className={`h-full ${failed ? 'bg-red-500' : done ? 'bg-green-500' : 'bg-blue-500'}`}
                                            style={{ width: `${Math.min(100, Math.round((run.completedStages / stageTotal) * 100))}%` }}
                                        />
                                    </div>
                                </div>
                            )}
                            {active && latest.type === 'pipeline_stage_retrying' && latest.error && (
                                <div className="mt-1 pl-4 text-xs text-yellow-500 truncate">{latest.error}</div>
                            )}
                        </div>
                    )
                })}
            </div>
        </div>
    )
}

interface DomainCardProps {
    subtask: SubtaskState
    childTask: Task | null
}

const DomainCard: React.FC<DomainCardProps> = ({ subtask, childTask }) => {
    const domainStatus = subtask.status
    const childSubtasks = childTask?.subtasks ?? []
    const doneCount = childSubtasks.filter(s => s.status === 'completed').length
    const totalCount = childSubtasks.length

    return (
        <div className="rounded-xl border border-white/[0.08] bg-white/[0.03] overflow-hidden">
            {/* Domain header */}
            <div className="flex items-center gap-3 px-4 py-3 border-b border-white/[0.05]">
                <div className={`w-2 h-2 rounded-full flex-shrink-0 ${statusColors[childTask?.status ?? domainStatus] ?? 'bg-gray-600'}`} />
                <div className="flex-1 min-w-0">
                    <div className="flex items-center gap-2">
                        <span className="text-xs font-semibold text-gray-300 uppercase tracking-wider">Domain</span>
                        <span className="text-sm font-medium text-white truncate">{subtask.name ?? subtask.goal.slice(0, 40)}</span>
                    </div>
                    {subtask.description && (
                        <p className="text-xs text-gray-500 mt-0.5 truncate">{subtask.description}</p>
                    )}
                </div>
                <span className="text-xs text-gray-500 flex-shrink-0">
                    {childTask ? (statusLabel[childTask.status] ?? childTask.status) : statusLabel[domainStatus]}
                </span>
            </div>

            {/* Child subtasks */}
            {childSubtasks.length > 0 && (
                <div className="px-4 py-3 space-y-2">
                    <p className="text-xs text-gray-600 mb-2">{doneCount}/{totalCount} subtasks complete</p>
                    {childSubtasks.map(cs => (
                        <div key={cs.id} className="flex items-center gap-2">
                            <div className={`w-1.5 h-1.5 rounded-full flex-shrink-0 ${statusColors[cs.status] ?? 'bg-gray-600'}`} />
                            <span className="text-xs text-gray-400 truncate">{cs.name ?? cs.goal.slice(0, 60)}</span>
                        </div>
                    ))}
                </div>
            )}

            {/* Planning state */}
            {childSubtasks.length === 0 && (childTask?.status === 'planning' || domainStatus === 'in_progress') && (
                <div className="flex items-center gap-2 px-4 py-3">
                    <BounceDots />
                    <span className="text-xs text-gray-500">Planning domain…</span>
                </div>
            )}
        </div>
    )
}

const TaskLiveView: React.FC<TaskLiveViewProps> = ({
    task,
    isPlanning,
    subtasks,
    childTasks,
    criteria,
    pipelineEvents,
    maestroMessage,
    maestroExpanded,
    expandedSubtasks,
    toolUsageBySubtask,
    agentMessagesBySubtask,
    onToggleMaestro,
    onToggleSubtask,
}) => {
    // Build a map from child_task_id → Task for domain subtask rendering
    const childTaskMap = new Map(childTasks.map(ct => [ct.id, ct]))

    return (
        <div className="flex flex-col gap-5 p-6">
            {task && (
                <div>
                    <p className="text-white font-medium leading-snug mb-3">{task.goal}</p>
                </div>
            )}

            {criteria && criteria.length > 0 && (
                <div className="rounded-lg border border-white/[0.06] bg-white/[0.02] px-4 py-3">
                    <p className="text-xs font-medium text-gray-500 uppercase tracking-wider mb-2">Acceptance Criteria</p>
                    <ul className="flex flex-col gap-1.5">
                        {criteria.map((item, i) => (
                            <li key={i} className="flex items-start gap-2 text-sm text-gray-400">
                                <span className="mt-1 w-1.5 h-1.5 rounded-full bg-gray-600 flex-shrink-0" />
                                {item}
                            </li>
                        ))}
                    </ul>
                </div>
            )}

            <PipelineProgress events={pipelineEvents} />

            {isPlanning && (
                <div className="flex items-center gap-3 py-2">
                    <BounceDots />
                    <span className="text-sm text-gray-500">Planning...</span>
                </div>
            )}

            {maestroMessage && (
                <div className="mb-4">
                    <MaestroCard
                        phase={maestroMessage.phase}
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
                        .map(subtask => {
                            if (subtask.agentId === 'maestro') {
                                const childTask = subtask.childTaskId ? childTaskMap.get(subtask.childTaskId) ?? null : null
                                return <DomainCard key={subtask.id} subtask={subtask} childTask={childTask} />
                            }
                            return (
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
                            )
                        })
                    }
                </div>
            )}
        </div>
    )
}

export default TaskLiveView
