import React from 'react'

export const FixNoteInput: React.FC<{
    note: string
    setNote: (v: string) => void
    onSubmit: () => void
    busy: boolean
    placeholder: string
}> = ({ note, setNote, onSubmit, busy, placeholder }) => (
    <div className="flex gap-2">
        <input value={note} onChange={e => setNote(e.target.value)}
            onKeyDown={e => { if (e.key === 'Enter' && note.trim()) onSubmit() }}
            placeholder={placeholder}
            className="flex-1 bg-black/40 border border-white/[0.1] rounded text-xs text-gray-200 px-2 py-1.5" />
        <button onClick={onSubmit} disabled={busy || !note.trim()}
            className="disabled:opacity-40 text-white px-3 py-1.5 rounded text-xs font-medium bg-blue-600/80 hover:bg-blue-700">
            Fix
        </button>
    </div>
)
