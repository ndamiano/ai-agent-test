import React from 'react'

export const FixNoteInput: React.FC<{
    note: string
    setNote: (v: string) => void
    onSubmit: () => void
    busy: boolean
    placeholder: string
    tone?: 'blue' | 'red'
}> = ({ note, setNote, onSubmit, busy, placeholder, tone = 'blue' }) => (
    <div className="flex gap-2">
        <input value={note} onChange={e => setNote(e.target.value)}
            onKeyDown={e => { if (e.key === 'Enter' && note.trim()) onSubmit() }}
            placeholder={placeholder}
            className={`flex-1 bg-black/40 border rounded text-xs text-gray-200 px-2 py-1.5 ${
                tone === 'red' ? 'border-red-500/20' : 'border-white/[0.1]'}`} />
        <button onClick={onSubmit} disabled={busy || !note.trim()}
            className={`disabled:opacity-40 text-white px-3 py-1.5 rounded text-xs font-medium ${
                tone === 'red' ? 'bg-red-600/80 hover:bg-red-600' : 'bg-blue-600/80 hover:bg-blue-700'}`}>
            Fix
        </button>
    </div>
)
