import React from 'react'
import { Button } from '../ui/Button'
import { SectionLabel, TextInput } from '../ui/Field'

export const FixNote: React.FC<{
    note: string
    setNote: (v: string) => void
    onSubmit: () => void
    busy: boolean
}> = ({ note, setNote, onSubmit, busy }) => (
    <section className="flex flex-col gap-2">
        <SectionLabel>Tell it what to change</SectionLabel>
        <TextInput value={note} onChange={e => setNote(e.target.value)}
            onKeyDown={e => { if (e.key === 'Enter' && note.trim()) onSubmit() }}
            placeholder="The lighthouse door doesn't open when I press E" />
        <div className="flex items-center gap-3 flex-wrap">
            <Button variant="primary" onClick={onSubmit} disabled={busy || !note.trim()}>Send it back</Button>
            <span className="text-xs text-dim">It keeps everything else and changes that.</span>
        </div>
    </section>
)
