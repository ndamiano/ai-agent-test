import React, { useState } from 'react'
import { Button } from '../ui/Button'
import { TextArea } from '../ui/Field'
import type { ConsoleReport } from './useConsoleReports'
import { reportsAsNote } from './useConsoleReports'

// Human-gated, one round: the deduped error list plus whatever the human adds goes through the
// SAME fix path as a hand-typed note. Nothing here fires without a click — the measured failure
// mode of automated fixing is the loop, not the signal.
export const ErrorFixModal: React.FC<{
    reports: ConsoleReport[]
    busy: boolean
    onSend: (note: string) => void
    onClose: () => void
}> = ({ reports, busy, onSend, onClose }) => {
    const [note, setNote] = useState('')

    const send = () => {
        const lines = reportsAsNote(reports)
        const human = note.trim()
        onSend(`${human ? `${human}\n\n` : ''}The game's console reported these errors:\n${lines}`)
    }

    return (
        <div className="fixed inset-0 z-50 bg-ink/80 grid place-items-center p-6" onClick={onClose}>
            <div className="w-full max-w-lg bg-panel border border-edge rounded-md p-5 flex flex-col gap-4"
                onClick={e => e.stopPropagation()}>
                <h3 className="font-display text-lg">The game hit console errors</h3>

                <div className="bg-well border border-edge rounded px-3 py-2 max-h-48 overflow-y-auto
                                font-mono text-xs leading-relaxed flex flex-col gap-1">
                    {reports.map(r => (
                        <div key={r.key} className="text-fail">
                            {r.message}
                            {r.count > 1 && <span className="text-dim"> ×{r.count}</span>}
                            {r.detail && <span className="text-dim"> — {r.detail}</span>}
                        </div>
                    ))}
                </div>

                <TextArea rows={2} value={note} onChange={e => setNote(e.target.value)} disabled={busy}
                    placeholder="Anything you noticed while playing (optional)" />

                <div className="flex items-center gap-3">
                    <Button variant="primary" size="md" onClick={send} disabled={busy}>
                        {busy ? 'Sending…' : 'Summon a mending'}
                    </Button>
                    <Button variant="ghost" size="md" onClick={onClose} disabled={busy}>Not now</Button>
                    <span className="text-xs text-dim">The model reads the game and these errors.</span>
                </div>
            </div>
        </div>
    )
}
