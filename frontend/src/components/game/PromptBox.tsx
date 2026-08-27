import React, { useEffect, useLayoutEffect, useRef, useState } from 'react'
import { SectionLabel, TextArea } from '../ui/Field'

// The user's own words, kept beside whatever builds so the design can be read against them.
export const AskLine: React.FC<{ ask: string }> = ({ ask }) => (
    <div className="flex flex-col gap-1">
        <span className="text-xs text-dim">You asked for</span>
        <p className="text-sm text-slate border-l-2 border-edge pl-3 whitespace-pre-wrap">{ask}</p>
    </div>
)

const MIN_HEIGHT_PX = 160

// The build's user message, editable. What is in this box is what the model reads, so it is shown
// as plain text rather than parsed into fields. Nothing is saved on its own — the text is sent
// with Build, which is the moment the user approves it.
export const PromptBox: React.FC<{
    ask: string
    prompt: string
    text: string
    onChange: (text: string) => void
}> = ({ ask, prompt, text, onChange }) => {
    const [dismissed, setDismissed] = useState(false)
    const box = useRef<HTMLTextAreaElement>(null)

    useEffect(() => { setDismissed(false) }, [prompt])

    // The text is a whole design document, so the box grows to hold it rather than scrolling
    // inside a fixed six rows.
    useLayoutEffect(() => {
        const el = box.current
        if (!el) return
        el.style.height = 'auto'
        el.style.height = `${Math.max(el.scrollHeight, MIN_HEIGHT_PX)}px`
    }, [text])

    const edited = text.trim() !== prompt.trim()

    return (
        <section className="flex flex-col gap-3">
            {ask && <AskLine ask={ask} />}
            <div className="flex items-center gap-3">
                <SectionLabel>The request</SectionLabel>
                {edited && !dismissed && (
                    <button onClick={() => { onChange(prompt); setDismissed(true) }}
                        className="ml-auto text-xs text-slate hover:text-bone transition-colors">
                        Undo edits
                    </button>
                )}
            </div>

            <TextArea ref={box} value={text} onChange={e => onChange(e.target.value)}
                spellCheck={false} placeholder="Describe the game you want."
                className="border-l-2 border-l-ember" />

            <p className="text-xs text-dim">
                {edited ? 'Rebuild uses this text.' : 'This exact text is the only thing the model is given.'}
            </p>
        </section>
    )
}
