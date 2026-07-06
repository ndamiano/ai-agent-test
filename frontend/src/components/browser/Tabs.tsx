import React from 'react'

// D1 — one tab per component type present in the run, driven entirely by `componentIds` (whatever
// the run's artifact actually has). A new module's component appears here with no code change.
const Tabs: React.FC<{
    componentIds: string[]
    active: string
    dirtyCounts: Record<string, number>
    onPick: (id: string) => void
}> = ({ componentIds, active, dirtyCounts, onPick }) => (
    <div className="flex gap-1 flex-wrap border-b border-white/[0.08]">
        {componentIds.map(id => (
            <button key={id} onClick={() => onPick(id)}
                className={`px-2.5 py-1.5 -mb-px text-xs font-semibold border-b-2 transition-colors flex items-center gap-1.5 ${
                    id === active ? 'text-white border-blue-500' : 'text-gray-500 border-transparent hover:text-gray-300'
                }`}>
                <span className="capitalize">{id.replace(/_/g, ' ')}</span>
                {(dirtyCounts[id] ?? 0) > 0 && (
                    <span className="px-1.5 py-0.5 rounded-full text-[10px] font-medium bg-amber-500/20 text-amber-400">
                        {dirtyCounts[id]}
                    </span>
                )}
            </button>
        ))}
    </div>
)

export default Tabs
