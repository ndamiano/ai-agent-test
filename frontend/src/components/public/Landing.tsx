import React, { useEffect, useState } from 'react'
import { api } from '../../api/client'
import { Button } from '../ui/Button'
import { SectionLabel } from '../ui/Field'
import { Sigil } from '../ui/Sigil'
import { Link } from '../../router'
import type { Demo } from '../../types'

const CLAMP_AT = 160

// One demo card: the prompt that summoned it, and the game itself — playable in place. The
// pairing IS the pitch; a video would be a weaker claim than the game running.
const DemoCard: React.FC<{ demo: Demo }> = ({ demo }) => {
    const [session, setSession] = useState<string | null>(null)
    const [busy, setBusy] = useState(false)
    const [expanded, setExpanded] = useState(false)
    const stageRef = React.useRef<HTMLDivElement>(null)
    const clamped = demo.prompt.length > CLAMP_AT && !expanded

    const play = async () => {
        setBusy(true)
        // Fullscreen the stage (not the iframe — it isn't mounted yet), synchronously,
        // while the click's user activation is still valid.
        stageRef.current?.requestFullscreen?.()?.catch(() => {})
        try { setSession((await api.demoPlaySession(demo.run_id)).url) }
        catch { /* listed-but-gone demo: the card just stays un-started */ }
        finally { setBusy(false) }
    }

    return (
        <div className="border border-edge rounded-md bg-panel overflow-hidden flex flex-col">
            <div ref={stageRef} className="relative aspect-video bg-sunken grid place-items-center">
                {session
                    ? (
                        <>
                            <iframe src={session} title={demo.title}
                                allow="fullscreen; gamepad; autoplay"
                                className="absolute inset-0 w-full h-full border-0" />
                            <button onClick={() => stageRef.current?.requestFullscreen?.()?.catch(() => {})}
                                title="fullscreen"
                                className="absolute top-2 right-2 px-2 py-1 rounded bg-ink/70 text-bone/80
                                           hover:text-bone text-sm transition-colors">
                                ⛶
                            </button>
                        </>
                    )
                    : (
                        <>
                            {demo.thumb_url
                                ? <img src={demo.thumb_url} alt=""
                                    className="absolute inset-0 w-full h-full object-cover opacity-80" />
                                : <div className="absolute inset-0 opacity-60"><Sigil seed={demo.run_id} /></div>}
                            <Button variant="primary" size="md" onClick={play} disabled={busy}
                                className="relative">
                                ▶ Play it
                            </Button>
                        </>
                    )}
            </div>
            <div className="px-4 py-3 flex flex-col gap-1.5">
                <SectionLabel>The words that summoned it</SectionLabel>
                <p className="text-sm text-slate leading-relaxed">
                    {clamped ? `${demo.prompt.slice(0, CLAMP_AT).trimEnd()}… ` : `${demo.prompt} `}
                    {demo.prompt.length > CLAMP_AT && (
                        <button onClick={() => setExpanded(e => !e)}
                            className="text-dim underline hover:text-slate transition-colors">
                            {expanded ? 'less' : 'see more'}
                        </button>
                    )}
                </p>
            </div>
        </div>
    )
}

const Landing: React.FC = () => {
    const [demos, setDemos] = useState<Demo[]>([])

    useEffect(() => { api.listDemos().then(setDemos).catch(() => setDemos([])) }, [])

    return (
        <div className="min-h-screen bg-ink">
            <header className="border-b border-edge bg-sunken px-6 py-3 flex items-center justify-between">
                <span className="font-display text-lg tracking-wide flex items-center gap-2.5">
                    <span className="w-[18px] h-[18px] rotate-45 border-[1.5px] border-ember relative
                                     after:absolute after:inset-[3px] after:bg-ember after:opacity-50" />
                    Maestro
                </span>
                <Link to="/login" className="text-sm text-slate hover:text-bone transition-colors">
                    Sign in
                </Link>
            </header>

            <main className="max-w-5xl mx-auto px-6 py-14 flex flex-col gap-14">
                <section className="text-center flex flex-col items-center gap-4">
                    <h1 className="font-display text-4xl md:text-5xl leading-tight max-w-2xl">
                        Describe a game and Maestro summons it into reality.
                    </h1>
                    <p className="text-slate max-w-xl leading-relaxed">
                        The game you imagine — a sentence, a paragraph, however you'd say it to a
                        friend — becomes a real, playable browser game. Art and all.
                    </p>
                </section>

                {demos.some(d => d.tier === 'showcase') && (
                    <section className="flex flex-col gap-5">
                        <h2 className="font-display text-2xl text-center">Iterated</h2>
                        <p className="text-sm text-slate text-center max-w-xl mx-auto">
                            Immediately playable, incrementally improved.
                        </p>
                        <div className="grid gap-6 md:grid-cols-2">
                            {demos.filter(d => d.tier === 'showcase')
                                .map(d => <DemoCard key={d.run_id} demo={d} />)}
                        </div>
                    </section>
                )}

                {demos.some(d => d.tier === 'oneshot') && (
                    <section className="flex flex-col gap-5">
                        <h2 className="font-display text-2xl text-center">One-shotted</h2>
                        <p className="text-sm text-slate text-center max-w-xl mx-auto">
                            No improvements made — didn't need any. You might still want some.
                        </p>
                        <div className="grid gap-6 md:grid-cols-2">
                            {demos.filter(d => d.tier === 'oneshot')
                                .map(d => <DemoCard key={d.run_id} demo={d} />)}
                        </div>
                    </section>
                )}

                {demos.length > 0 && (
                    <p className="text-xs text-dim text-center">
                        Every one of these was made by the machine from the words shown under it.
                    </p>
                )}
            </main>
        </div>
    )
}

export default Landing
