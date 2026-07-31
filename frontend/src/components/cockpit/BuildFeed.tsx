import React, { useEffect, useRef } from 'react'
import type { FeedEntry, FeedTone } from '../../hooks/useRunBuildStream'

const TONE: Record<FeedTone, string> = {
    info: 'text-gray-400',
    good: 'text-green-400',
    bad: 'text-red-400',
    warn: 'text-amber-400',
}

// A long build emits hundreds of step events; only the tail is worth showing (and worth mounting).
const MAX_ROWS = 200

export const BuildFeed: React.FC<{ feed: FeedEntry[] }> = ({ feed }) => {
    const endRef = useRef<HTMLDivElement>(null)
    useEffect(() => { endRef.current?.scrollIntoView({ behavior: 'smooth' }) }, [feed.length])

    const rows = feed.length > MAX_ROWS ? feed.slice(-MAX_ROWS) : feed
    return (
        <div className="flex flex-col">
            <div className="text-gray-400 text-[10px] font-semibold uppercase tracking-wide mb-1">Build feed</div>
            <div className="bg-black/40 border border-white/[0.06] rounded h-36 overflow-y-auto px-2.5 py-1.5 font-mono text-[11px] space-y-0.5">
                {rows.length === 0
                    ? <div className="text-gray-600">no activity yet</div>
                    : rows.map(e => <div key={e.key} className={TONE[e.tone]}>{e.text}</div>)}
                <div ref={endRef} />
            </div>
        </div>
    )
}
