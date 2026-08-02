import React, { useEffect, useRef, useState } from 'react'
import type { FeedEntry, FeedTone } from '../../hooks/useRunBuildStream'

const TONE: Record<FeedTone, string> = {
    info: 'text-slate',
    good: 'text-live',
    bad: 'text-fail',
    warn: 'text-wait',
}

// A long build emits hundreds of step events; only the tail is worth showing (and worth mounting).
const MAX_ROWS = 200

export const BuildLog: React.FC<{ feed: FeedEntry[]; defaultOpen?: boolean }> = ({
    feed, defaultOpen = false,
}) => {
    const [open, setOpen] = useState(defaultOpen)
    const boxRef = useRef<HTMLDivElement>(null)

    // Scroll the log's own container, never scrollIntoView — that walks every scrollable ancestor
    // and yanks the whole page down a line per event whenever the page itself scrolls.
    useEffect(() => {
        if (open && boxRef.current) boxRef.current.scrollTop = boxRef.current.scrollHeight
    }, [feed.length, open])

    const rows = feed.length > MAX_ROWS ? feed.slice(-MAX_ROWS) : feed

    return (
        <div className="flex flex-col gap-2 border-t border-bone/[0.09] pt-3">
            <button onClick={() => setOpen(o => !o)}
                className="flex items-center gap-2 text-sm text-slate hover:text-bone transition-colors self-start">
                <span className="text-dim text-xs">{open ? '▾' : '▸'}</span>
                Summoning log
                <span className="font-mono text-xs text-dim">{feed.length}</span>
            </button>
            {open && (
                <div ref={boxRef}
                    className="bg-well border border-edge rounded px-3 py-2 h-40 overflow-y-auto
                               font-mono text-xs leading-relaxed">
                    {rows.length === 0
                        ? <div className="text-dim">the circle is quiet</div>
                        : rows.map(e => <div key={e.key} className={TONE[e.tone]}>{e.text}</div>)}
                </div>
            )}
        </div>
    )
}
