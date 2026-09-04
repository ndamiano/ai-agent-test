import React, { useEffect, useState } from 'react'
import { api, buildErrorMessage } from '../../api/client'
import { track } from '../../api/track'
import { useAuth } from '../../contexts/AuthContext'
import { Button } from '../ui/Button'
import { TextArea } from '../ui/Field'

// Openings that produce games, shown so an empty box is a starting line rather than a blank.
const EXAMPLES = [
    'A crab running a small bank, approving or denying loans to sea creatures based on their collateral.',
    'Stack increasingly unreasonable objects on a very patient turtle.',
    'A tea shop for forest animals where you have to remember their orders.',
    "You're a firefly; the only control makes you glow, attracting moths and alerting frogs.",
]

const shorten = (s: string, n = 46) => (s.length <= n ? s : `${s.slice(0, n).trimEnd()}…`)

export const CreateGame: React.FC<{ onCreated: (runId: string) => void; onCancel: () => void }> = ({
    onCreated, onCancel,
}) => {
    const [text, setText] = useState('')
    const [busy, setBusy] = useState(false)
    const [error, setError] = useState<string | null>(null)
    const { refreshBalance } = useAuth()

    useEffect(() => { track('create_opened') }, [])

    const create = async () => {
        setBusy(true); setError(null)
        try {
            const { run_id } = await api.createGame(text)
            onCreated(run_id)
        } catch (e) {
            setError(buildErrorMessage(e, 'Could not start the design'))
        } finally { setBusy(false); refreshBalance() }
    }

    return (
        <div className="h-full overflow-y-auto">
            <div className="max-w-2xl mx-auto px-6 py-10 flex flex-col gap-5">
                <button onClick={onCancel}
                    className="text-xs text-slate hover:text-bone transition-colors self-start">
                    ← All games
                </button>

                <div className="flex flex-col gap-1.5 text-center">
                    <h2 className="font-display text-3xl">What should exist?</h2>
                    <p className="text-sm text-slate">
                        Your words become a systems design, and the game is built from it.
                    </p>
                </div>

                <TextArea value={text} onChange={e => setText(e.target.value)} disabled={busy}
                    rows={5} autoFocus spellCheck={false}
                    placeholder="A snail racing game where the race runs over four in-game days."
                    className="border-l-2 border-l-ember" />

                <div className="flex items-center gap-3 flex-wrap">
                    <Button variant="primary" size="md" onClick={create} disabled={busy || !text.trim()}>
                        {busy ? 'Starting…' : 'Summon it · 1 credit'}
                    </Button>
                    <span className="text-xs text-dim">
                        One credit covers the design and the build. It takes a while.
                    </span>
                </div>

                {error && <p className="text-fail text-sm">{error}</p>}

                <div className="flex flex-col gap-2 mt-2">
                    <span className="text-xs text-dim">Or start from one of these</span>
                    <div className="flex flex-wrap gap-2">
                        {EXAMPLES.map(e => (
                            <button key={e} onClick={() => setText(e)} disabled={busy}
                                className="text-sm text-slate border border-edge rounded-full px-3 py-1
                                           hover:text-bone hover:border-ember/50 transition-colors disabled:opacity-40">
                                {shorten(e)}
                            </button>
                        ))}
                    </div>
                </div>
            </div>
        </div>
    )
}
