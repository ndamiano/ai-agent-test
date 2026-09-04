import React, { useEffect, useLayoutEffect, useRef, useState } from 'react'
import { SectionLabel, TextArea } from '../ui/Field'
import { parseDesign } from './design'

// The user's own words, kept beside whatever builds so the design can be read against them.
export const AskLine: React.FC<{ ask: string }> = ({ ask }) => (
    <div className="flex flex-col gap-1">
        <span className="text-xs text-dim">You asked for</span>
        <p className="text-sm text-slate border-l-2 border-edge pl-3 whitespace-pre-wrap">{ask}</p>
    </div>
)

const MIN_HEIGHT_PX = 160

// The design as headed sections. Derived from the text on every render and never stored, so
// there is no second copy to drift from what builds.
const DesignView: React.FC<{ text: string }> = ({ text }) => {
    const view = parseDesign(text)
    return (
        <div className="flex flex-col gap-4 border-l-2 border-l-ember pl-4 py-1">
            {view.lead && <p className="text-[15px] leading-relaxed text-bone">{view.lead}</p>}
            {view.systems.length > 0 && (
                <ul className="flex flex-wrap gap-1.5" aria-label="systems">
                    {view.systems.map(s => (
                        <li key={s} className="text-xs text-slate border border-edge rounded-full px-2.5 py-0.5">{s}</li>
                    ))}
                </ul>
            )}
            {view.sections.map((s, i) => (
                <section key={i} className="flex flex-col gap-1">
                    {s.name && <h4 className="text-xs font-mono uppercase tracking-[0.13em] text-ember">{s.name}</h4>}
                    <p className="text-sm leading-relaxed text-slate whitespace-pre-wrap">{s.body}</p>
                </section>
            ))}
        </div>
    )
}

// The build's user message. Read as headed sections or edited as the text itself — both views
// are the ONE string, and that string is what a rebuild sends. Nothing is saved on its own: the
// text goes with Rebuild.
export const PromptBox: React.FC<{
    ask: string
    prompt: string
    text: string
    onChange: (text: string) => void
}> = ({ ask, prompt, text, onChange }) => {
    const [dismissed, setDismissed] = useState(false)
    const [editing, setEditing] = useState(false)
    const box = useRef<HTMLTextAreaElement>(null)

    useEffect(() => { setDismissed(false) }, [prompt])

    // The text is a whole design document, so the box grows to hold it rather than scrolling
    // inside a fixed six rows.
    useLayoutEffect(() => {
        const el = box.current
        if (!el) return
        el.style.height = 'auto'
        el.style.height = `${Math.max(el.scrollHeight, MIN_HEIGHT_PX)}px`
    }, [text, editing])

    const edited = text.trim() !== prompt.trim()

    return (
        <section className="flex flex-col gap-3">
            {ask && <AskLine ask={ask} />}
            <div className="flex items-center gap-3">
                <SectionLabel>The design</SectionLabel>
                <button onClick={() => setEditing(e => !e)}
                    className="text-xs text-slate hover:text-bone transition-colors">
                    {editing ? 'Done editing' : 'Edit'}
                </button>
                {edited && !dismissed && (
                    <button onClick={() => { onChange(prompt); setDismissed(true) }}
                        className="ml-auto text-xs text-slate hover:text-bone transition-colors">
                        Undo edits
                    </button>
                )}
            </div>

            {editing ? (
                <TextArea ref={box} value={text} onChange={e => onChange(e.target.value)}
                    spellCheck={false} placeholder="Describe the game you want."
                    className="border-l-2 border-l-ember" />
            ) : (
                <DesignView text={text} />
            )}

            <p className="text-xs text-dim">
                {edited ? 'Rebuild uses this text.' : 'This exact text is the only thing the model is given.'}
            </p>
        </section>
    )
}
