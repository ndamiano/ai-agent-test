import React from 'react'
import { ChevronLeft, ChevronRight, AlertCircle } from 'lucide-react'
import type { Asset } from '../../types'

// D2 — position indicator + prev/next within the current component type, plus D7's
// "jump to next dirty" and nav-strip dirty markers so flagged items are findable at a glance.
const NavStrip: React.FC<{
    assets: Asset[]
    activeIndex: number
    onPick: (index: number) => void
}> = ({ assets, activeIndex, onPick }) => {
    const dirtyCount = assets.filter(a => a.dirty).length

    const jumpToNextDirty = () => {
        for (let step = 1; step <= assets.length; step++) {
            const idx = (activeIndex + step) % assets.length
            if (assets[idx].dirty) { onPick(idx); return }
        }
    }

    return (
        <div className="space-y-2">
            <div className="flex items-center gap-2">
                <button onClick={() => onPick(Math.max(0, activeIndex - 1))} disabled={activeIndex <= 0}
                    className="text-gray-500 hover:text-gray-200 disabled:opacity-30" title="previous">
                    <ChevronLeft size={16} />
                </button>
                <span className="inline-block w-44 text-center truncate text-gray-400 text-xs font-medium"
                    title={assets[activeIndex]?.id ?? '—'}>
                    {assets[activeIndex]?.id ?? '—'} · {assets.length ? activeIndex + 1 : 0} of {assets.length}
                </span>
                <button onClick={() => onPick(Math.min(assets.length - 1, activeIndex + 1))} disabled={activeIndex >= assets.length - 1}
                    className="text-gray-500 hover:text-gray-200 disabled:opacity-30" title="next">
                    <ChevronRight size={16} />
                </button>
                <button onClick={jumpToNextDirty} disabled={dirtyCount === 0}
                    title="jump to the next flagged item"
                    className="ml-auto flex items-center gap-1 text-amber-400 hover:text-amber-300 disabled:opacity-30 disabled:text-gray-600 text-xs">
                    <AlertCircle size={12} /> next dirty{dirtyCount > 0 ? ` (${dirtyCount})` : ''}
                </button>
            </div>
            <div className="flex gap-1.5 overflow-x-auto pb-1">
                {assets.map((a, i) => (
                    <button key={a.idkey} onClick={() => onPick(i)}
                        className={`shrink-0 relative px-2 py-1 rounded text-[11px] font-mono border transition-colors ${
                            i === activeIndex ? 'bg-white/[0.1] border-white/20 text-white' : 'bg-white/[0.03] border-white/[0.06] text-gray-500 hover:text-gray-300'
                        }`}>
                        {a.dirty && <span className="absolute -top-0.5 -right-0.5 w-1.5 h-1.5 rounded-full bg-amber-400" />}
                        {a.id}
                    </button>
                ))}
            </div>
        </div>
    )
}

export default NavStrip
