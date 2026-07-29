import React, { useEffect, useState } from 'react'

// The build's user message, editable. What is in this box is what the model reads, so it is shown
// as plain text rather than parsed into fields. Nothing is saved on its own — the text is sent
// with Build, which is the moment the user approves it.
export const PromptCard: React.FC<{
    prompt: string
    disabled: boolean
    text: string
    onChange: (text: string) => void
}> = ({ prompt, disabled, text, onChange }) => {
    const [dismissed, setDismissed] = useState(false)

    useEffect(() => { setDismissed(false) }, [prompt])

    const edited = text.trim() !== prompt.trim()

    return (
        <section className="space-y-2">
            <div className="flex items-center gap-2">
                <h3 className="text-gray-300 text-xs font-semibold uppercase tracking-wide">Prompt</h3>
                <span className="text-gray-600 text-[11px]">this exact text builds the game</span>
                {edited && !dismissed && (
                    <button onClick={() => { onChange(prompt); setDismissed(true) }}
                        className="text-gray-500 hover:text-gray-300 text-[11px] ml-auto">revert</button>
                )}
            </div>

            <textarea
                value={text}
                onChange={e => onChange(e.target.value)}
                disabled={disabled}
                rows={6}
                spellCheck={false}
                placeholder="Describe the game you want."
                className="w-full bg-[#1a1a1a] border border-white/[0.08] focus:border-blue-500/60 focus:outline-none
                           rounded-lg px-3 py-2.5 text-gray-200 text-sm leading-relaxed resize-y
                           disabled:opacity-60 disabled:cursor-not-allowed"
            />

            <p className="text-gray-600 text-[11px]">
                {disabled
                    ? 'Editable when no build is running.'
                    : edited
                        ? 'Edited — Build uses this text.'
                        : 'Edit it here, then press Build.'}
            </p>
        </section>
    )
}
