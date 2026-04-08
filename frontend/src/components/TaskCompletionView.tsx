import React from 'react'
import { FadeSlideIn } from './FadeSlideIn'
import type { ArtifactManifest } from '../types'
import type { SubtaskState } from '../hooks/useTaskStage'

interface TaskCompletionViewProps {
    phase: 'done' | 'failed'
    artifact: ArtifactManifest | null
    goal: string | undefined
    subtasks: SubtaskState[]
    agentsExpanded: boolean
    onToggleAgents: () => void
}

// ── Helpers ────────────────────────────────────────────────────────────────

/** Map a file extension (or artifact type) to a simple SVG icon path data string. */
function getFileIcon(path: string, type: string): string {
    const ext = path.split('.').pop()?.toLowerCase() ?? ''

    if (type === 'zip' || ext === 'zip' || ext === 'tar' || ext === 'gz') {
        // Archive icon
        return 'M9 13h6m-3-3v6m-9 1V7a2 2 0 012-2h6l2 2h6a2 2 0 012 2v8a2 2 0 01-2 2H5a2 2 0 01-2-2z'
    }
    if (['png', 'jpg', 'jpeg', 'gif', 'svg', 'webp'].includes(ext)) {
        // Image icon
        return 'M4 16l4.586-4.586a2 2 0 012.828 0L16 16m-2-2l1.586-1.586a2 2 0 012.828 0L20 14m-6-6h.01M6 20h12a2 2 0 002-2V6a2 2 0 00-2-2H6a2 2 0 00-2 2v12a2 2 0 002 2z'
    }
    if (['json', 'yaml', 'yml', 'toml', 'xml'].includes(ext)) {
        // Code/data icon
        return 'M10 20l4-16m4 4l4 4-4 4M6 16l-4-4 4-4'
    }
    if (['csv', 'xls', 'xlsx'].includes(ext)) {
        // Spreadsheet icon
        return 'M9 17V7m0 10a2 2 0 01-2 2H5a2 2 0 01-2-2V7a2 2 0 012-2h2a2 2 0 012 2m0 10a2 2 0 002 2h2a2 2 0 002-2M9 7a2 2 0 012-2h2a2 2 0 012 2m0 10V7m0 10a2 2 0 002 2h2a2 2 0 002-2V7a2 2 0 00-2-2h-2a2 2 0 00-2 2'
    }
    if (['md', 'txt', 'rst'].includes(ext)) {
        // Document/text icon
        return 'M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z'
    }
    if (['py', 'js', 'ts', 'tsx', 'jsx', 'sh', 'rb', 'go', 'rs'].includes(ext)) {
        // Code icon
        return 'M10 20l4-16m4 4l4 4-4 4M6 16l-4-4 4-4'
    }
    // Default: generic file
    return 'M7 21h10a2 2 0 002-2V9.414a1 1 0 00-.293-.707l-5.414-5.414A1 1 0 0012.586 3H7a2 2 0 00-2 2v14a2 2 0 002 2z'
}

/** Build the download URL for an artifact path. */
function downloadUrl(artifactPath: string): string {
    // The backend strips the "outputs/" prefix automatically, so we can pass the path verbatim.
    const encoded = artifactPath.split('/').map(encodeURIComponent).join('/')
    return `/api/outputs/${encoded}`
}

// ── Sub-component ──────────────────────────────────────────────────────────

interface ArtifactButtonProps {
    label: string
    path: string
    type: string
}

const ArtifactButton: React.FC<ArtifactButtonProps> = ({ label, path, type }) => {
    const href = downloadUrl(path)
    const iconPath = getFileIcon(path, type)
    const isZip = type === 'zip' || path.endsWith('.zip')

    return (
        <a
            href={href}
            download
            onClick={(e) => e.stopPropagation()}
            className={[
                'group inline-flex items-center gap-2.5 px-4 py-2.5 rounded-lg border text-sm font-medium transition-all duration-150',
                isZip
                    ? 'bg-blue-500/10 border-blue-500/30 text-blue-300 hover:bg-blue-500/20 hover:border-blue-400/50 hover:text-blue-200'
                    : 'bg-white/5 border-white/10 text-gray-300 hover:bg-white/10 hover:border-white/20 hover:text-white',
            ].join(' ')}
            title={`Download ${path}`}
        >
            {/* File type icon */}
            <svg
                className={[
                    'w-4 h-4 flex-shrink-0 transition-transform duration-150 group-hover:scale-110',
                    isZip ? 'text-blue-400' : 'text-gray-400 group-hover:text-gray-300',
                ].join(' ')}
                fill="none"
                viewBox="0 0 24 24"
                stroke="currentColor"
                strokeWidth={1.5}
            >
                <path strokeLinecap="round" strokeLinejoin="round" d={iconPath} />
            </svg>

            {/* Label */}
            <span className="truncate max-w-[180px]">{label}</span>

            {/* Download arrow — appears on hover */}
            <svg
                className="w-3.5 h-3.5 flex-shrink-0 opacity-0 group-hover:opacity-100 transition-opacity duration-150 ml-auto"
                fill="none"
                viewBox="0 0 24 24"
                stroke="currentColor"
                strokeWidth={2}
            >
                <path strokeLinecap="round" strokeLinejoin="round" d="M4 16v1a3 3 0 003 3h10a3 3 0 003-3v-1m-4-4l-4 4m0 0l-4-4m4 4V4" />
            </svg>
        </a>
    )
}

// ── Main component ─────────────────────────────────────────────────────────

const TaskCompletionView: React.FC<TaskCompletionViewProps> = ({
    phase,
    artifact,
    goal,
    subtasks,
    agentsExpanded,
    onToggleAgents,
}) => {
    return (
        <div className="transition-all duration-500 ease-out opacity-100 translate-y-0">
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
                        <div className="px-6 py-6 space-y-5">
                            <p className="text-gray-300 leading-relaxed whitespace-pre-wrap">
                                {artifact.summary}
                            </p>

                            {artifact.artifacts.length > 0 && (
                                <div className="space-y-2">
                                    <p className="text-xs font-medium text-gray-500 uppercase tracking-wider">
                                        Output files
                                    </p>
                                    <div className="flex flex-wrap gap-2">
                                        {artifact.artifacts.map((a, i) => (
                                            <ArtifactButton
                                                key={i}
                                                label={a.label}
                                                path={a.path}
                                                type={a.type}
                                            />
                                        ))}
                                    </div>
                                </div>
                            )}
                        </div>
                    </FadeSlideIn>
                </div>
            ) : phase === 'failed' ? (
                <div className="border-b border-white/10">
                    <FadeSlideIn delay={0}>
                        <div className="px-6 py-4 flex items-center gap-3 border-b border-red-500/20 bg-red-500/5">
                            <div className="w-2 h-2 rounded-full bg-red-500 shadow-[0_0_8px_rgba(239,68,68,0.8)]" />
                            <span className="text-sm font-medium text-red-400">Failed</span>
                            <span className="text-sm text-gray-500 ml-auto truncate max-w-xs">
                                {goal}
                            </span>
                        </div>
                    </FadeSlideIn>
                </div>
            ) : null}

            {/* Agent / subtask details */}
            {subtasks.length > 0 && (
                <FadeSlideIn delay={160}>
                    <div className="px-6 py-4">
                        <button
                            onClick={onToggleAgents}
                            className="flex items-center gap-2 text-sm text-gray-500 hover:text-gray-300 transition-colors"
                        >
                            <svg
                                className={`w-3.5 h-3.5 transition-transform duration-200 ${agentsExpanded ? 'rotate-90' : ''}`}
                                fill="none"
                                viewBox="0 0 24 24"
                                stroke="currentColor"
                                strokeWidth={2}
                            >
                                <path strokeLinecap="round" strokeLinejoin="round" d="M9 5l7 7-7 7" />
                            </svg>
                            {subtasks.length} subtask{subtasks.length !== 1 ? 's' : ''}
                        </button>

                        {agentsExpanded && (
                            <div className="mt-3 space-y-2">
                                {subtasks.map((s) => (
                                    <div
                                        key={s.id}
                                        className="flex items-center gap-2 px-3 py-2 rounded-lg bg-white/5 border border-white/10"
                                    >
                                        <div className={`w-1.5 h-1.5 rounded-full ${
                                            s.status === 'completed' ? 'bg-green-500' :
                                            s.status === 'failed' ? 'bg-red-500' :
                                            s.status === 'in_progress' ? 'bg-blue-500' :
                                            'bg-gray-500'
                                        }`} />
                                        <span className="text-sm text-gray-300">{s.name || s.goal}</span>
                                    </div>
                                ))}
                            </div>
                        )}
                    </div>
                </FadeSlideIn>
            )}
        </div>
    )
}

export default TaskCompletionView