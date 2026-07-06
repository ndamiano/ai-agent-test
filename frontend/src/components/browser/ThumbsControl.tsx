import React, { useState } from 'react'
import { ThumbsUp, ThumbsDown } from 'lucide-react'

// D6 — thumbs-up (clear dirty) / thumbs-down (set dirty + "what's wrong?" note) on every card.
const ThumbsControl: React.FC<{
    dirty: boolean
    busy: boolean
    onThumbsUp: () => void
    onThumbsDown: (note: string) => void
}> = ({ dirty, busy, onThumbsUp, onThumbsDown }) => {
    const [showNote, setShowNote] = useState(false)
    const [note, setNote] = useState('')

    const submitDown = () => {
        onThumbsDown(note)
        setNote('')
        setShowNote(false)
    }

    return (
        <div className="flex items-center gap-2">
            <button onClick={onThumbsUp} disabled={busy} title="looks good — clear the flag"
                className={`p-1 rounded hover:bg-white/[0.08] disabled:opacity-40 ${!dirty ? 'text-green-400' : 'text-gray-500 hover:text-green-400'}`}>
                <ThumbsUp size={14} />
            </button>
            <button onClick={() => setShowNote(v => !v)} disabled={busy} title="change this"
                className={`p-1 rounded hover:bg-white/[0.08] disabled:opacity-40 ${dirty ? 'text-amber-400' : 'text-gray-500 hover:text-amber-400'}`}>
                <ThumbsDown size={14} />
            </button>
            {showNote && (
                <div className="flex gap-1.5 flex-1 min-w-0">
                    <input value={note} onChange={e => setNote(e.target.value)}
                        onKeyDown={e => { if (e.key === 'Enter' && note.trim()) submitDown() }}
                        placeholder="what's wrong? (becomes the rewrite note)"
                        className="flex-1 min-w-0 bg-black/40 border border-white/[0.1] rounded text-xs text-gray-200 px-2 py-1" />
                    <button onClick={submitDown} disabled={busy || !note.trim()}
                        className="bg-amber-600/80 hover:bg-amber-700 disabled:opacity-40 text-white px-2 py-1 rounded text-xs whitespace-nowrap">
                        Flag
                    </button>
                </div>
            )}
        </div>
    )
}

export default ThumbsControl
