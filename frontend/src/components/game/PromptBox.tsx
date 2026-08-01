import React, { useEffect, useState } from 'react'
import { SectionLabel, TextArea } from '../ui/Field'

// The build's user message, editable. What is in this box is what the model reads, so it is shown
// as plain text rather than parsed into fields. Nothing is saved on its own — the text is sent
// with Build, which is the moment the user approves it.
export const PromptBox: React.FC<{
    prompt: string
    disabled: boolean
    text: string
    onChange: (text: string) => void
}> = ({ prompt, disabled, text, onChange }) => {
    const [dismissed, setDismissed] = useState(false)

    useEffect(() => { setDismissed(false) }, [prompt])

    const edited = text.trim() !== prompt.trim()

    return (
        <section className="flex flex-col gap-2">
            <div className="flex items-center gap-3">
                <SectionLabel>The request</SectionLabel>
                {edited && !dismissed && (
                    <button onClick={() => { onChange(prompt); setDismissed(true) }}
                        className="ml-auto text-xs text-slate hover:text-bone transition-colors">
                        Undo edits
                    </button>
                )}
            </div>

            <TextArea value={text} onChange={e => onChange(e.target.value)} disabled={disabled}
                rows={6} spellCheck={false} placeholder="Describe the game you want."
                className="border-l-2 border-l-ember" />

            <p className="text-xs text-dim">
                {disabled
                    ? 'Editable when nothing is running.'
                    : edited
                        ? 'Rebuild uses this text.'
                        : 'This exact text is the only thing the model is given.'}
            </p>
        </section>
    )
}
