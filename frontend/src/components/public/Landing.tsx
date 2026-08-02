import React, { useEffect, useState } from 'react'
import { api } from '../../api/client'
import { Button } from '../ui/Button'
import { SectionLabel } from '../ui/Field'
import { Sigil } from '../ui/Sigil'
import { Link } from '../../router'

interface Demo { run_id: string; title: string; prompt: string }

// One demo card: the prompt that summoned it, and the game itself — playable in place. The
// pairing IS the pitch; a video would be a weaker claim than the game running.
const DemoCard: React.FC<{ demo: Demo }> = ({ demo }) => {
    const [session, setSession] = useState<string | null>(null)
    const [busy, setBusy] = useState(false)
    const frameRef = React.useRef<HTMLIFrameElement>(null)

    const play = async () => {
        setBusy(true)
        try { setSession((await api.demoPlaySession(demo.run_id)).url) }
        catch { /* listed-but-gone demo: the card just stays un-started */ }
        finally { setBusy(false) }
    }

    return (
        <div className="border border-edge rounded-md bg-panel overflow-hidden flex flex-col">
            <div className="relative aspect-video bg-sunken grid place-items-center">
                {session
                    ? (
                        <>
                            <iframe ref={frameRef} src={session} title={demo.title}
                                allow="fullscreen; gamepad; autoplay"
                                className="absolute inset-0 w-full h-full border-0" />
                            <button onClick={() => frameRef.current?.requestFullscreen()}
                                title="fullscreen"
                                className="absolute top-2 right-2 px-2 py-1 rounded bg-ink/70 text-bone/80
                                           hover:text-bone text-sm transition-colors">
                                ⛶
                            </button>
                        </>
                    )
                    : (
                        <>
                            <div className="absolute inset-0 opacity-60"><Sigil seed={demo.run_id} /></div>
                            <Button variant="primary" size="md" onClick={play} disabled={busy}
                                className="relative">
                                ▶ Play it
                            </Button>
                        </>
                    )}
            </div>
            <div className="px-4 py-3 flex flex-col gap-1.5">
                <SectionLabel>The words that summoned it</SectionLabel>
                <p className="text-sm text-slate leading-relaxed">{demo.prompt}</p>
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
                        Describe a game. A while later, it exists.
                    </h1>
                    <p className="text-slate max-w-xl leading-relaxed">
                        Maestro takes the game you imagine — a sentence, a paragraph, however you'd
                        say it to a friend — and summons a real, playable browser game from it.
                        Art and all.
                    </p>
                    <p className="text-xs text-dim">
                        Private alpha. Accounts are limited — if you have one,{' '}
                        <Link to="/login" className="underline hover:text-slate">sign in</Link>.
                    </p>
                </section>

                {demos.length > 0 && (
                    <section className="flex flex-col gap-5">
                        <h2 className="font-display text-2xl text-center">Played straight from the cauldron</h2>
                        <div className="grid gap-6 md:grid-cols-2">
                            {demos.map(d => <DemoCard key={d.run_id} demo={d} />)}
                        </div>
                        <p className="text-xs text-dim text-center">
                            Every one of these was made by the machine from the words shown under it.
                        </p>
                    </section>
                )}
            </main>
        </div>
    )
}

export default Landing
