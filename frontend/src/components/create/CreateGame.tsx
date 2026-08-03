import React, { useState } from 'react'
import { api, buildErrorMessage } from '../../api/client'
import { useAuth } from '../../contexts/AuthContext'
import { Button } from '../ui/Button'
import { TextArea } from '../ui/Field'
import { EnhanceNotice, shouldShowNotice } from './EnhanceNotice'

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
    const [enhance, setEnhance] = useState(true)
    const [notice, setNotice] = useState(false)
    // A returned plan puts the page in review: the user reads and edits the stages, and pressing
    // Build on them is the approval — what is on screen is byte for byte what builds.
    const [planRunId, setPlanRunId] = useState<string | null>(null)
    const [stages, setStages] = useState<string[] | null>(null)
    const { refreshBalance } = useAuth()

    const createPlain = async () => {
        setBusy(true); setError(null)
        try {
            const { run_id } = await api.createGame(text)
            onCreated(run_id)
        } catch (e) {
            setError(buildErrorMessage(e, 'Could not start the build'))
        } finally { setBusy(false); refreshBalance() }
    }

    const plan = async () => {
        setBusy(true); setError(null); setEnhance(true)
        try {
            const res = await api.enhancePrompt(text)
            if (res.stages.length < 2) {
                // A plan that came back as one stage has nothing to review — build it plain.
                const { run_id } = await api.buildGame(res.run_id, text)
                onCreated(run_id); return
            }
            setPlanRunId(res.run_id)
            setStages(res.stages)
        } catch (e) {
            setError(buildErrorMessage(e, 'Could not plan the build'))
        } finally { setBusy(false); refreshBalance() }
    }

    const buildPlanned = async () => {
        if (!planRunId || !stages) return
        setBusy(true); setError(null)
        try {
            const { run_id } = await api.buildStages(planRunId, stages)
            onCreated(run_id)
        } catch (e) {
            setError(buildErrorMessage(e, 'Could not start the build'))
        } finally { setBusy(false); refreshBalance() }
    }

    const create = () => {
        if (enhance) { void plan(); return }
        if (shouldShowNotice()) { setNotice(true); return }
        void createPlain()
    }

    const reviewing = stages !== null

    return (
        <div className="h-full overflow-y-auto">
            <div className="max-w-2xl mx-auto px-6 py-10 flex flex-col gap-5">
                <button onClick={onCancel}
                    className="text-xs text-slate hover:text-bone transition-colors self-start">
                    ← All games
                </button>

                <div className="flex flex-col gap-1.5 text-center">
                    <h2 className="font-display text-3xl">{reviewing ? 'The build plan' : 'What should exist?'}</h2>
                    <p className="text-sm text-slate">
                        {reviewing
                            ? 'Your words, planned into stages. Edit anything — these exact texts are what builds.'
                            : 'This exact text is the only thing the model is given.'}
                    </p>
                </div>

                {reviewing ? (
                    <>
                        <div className="flex flex-col gap-1">
                            <span className="text-xs text-dim">You asked for</span>
                            <p className="text-sm text-slate border-l-2 border-edge pl-3 whitespace-pre-wrap">{text}</p>
                        </div>
                        {stages.map((s, i) => (
                            <div key={i} className="flex flex-col gap-1">
                                <span className="text-xs text-dim">
                                    {i === 0 ? 'Stage 1 — built first, playable on its own' : `Stage ${i + 1} — added once the game runs`}
                                </span>
                                <TextArea value={s} disabled={busy} rows={4} spellCheck={false}
                                    onChange={e => setStages(stages.map((t, j) => (j === i ? e.target.value : t)))}
                                    className="border-l-2 border-l-ember" />
                            </div>
                        ))}
                        <div className="flex items-center gap-3 flex-wrap">
                            <Button variant="primary" size="md" onClick={buildPlanned}
                                disabled={busy || stages.every(s => !s.trim())}>
                                {busy ? 'Starting…' : 'Build it · 1 credit'}
                            </Button>
                            <Button variant="ghost" size="md" disabled={busy}
                                onClick={() => { setStages(null); setPlanRunId(null) }}>
                                Back to my words
                            </Button>
                        </div>
                    </>
                ) : (
                    <>
                        <TextArea value={text} onChange={e => setText(e.target.value)} disabled={busy}
                            rows={5} autoFocus spellCheck={false}
                            placeholder="A snail racing game where the race runs over four in-game days."
                            className="border-l-2 border-l-ember" />

                        <div className="flex items-center gap-3 flex-wrap">
                            <Button variant="primary" size="md" onClick={create} disabled={busy || !text.trim()}>
                                {busy ? (enhance ? 'Planning…' : 'Starting…') : 'Build it · 1 credit'}
                            </Button>
                            <label className="flex items-center gap-2 text-xs text-dim cursor-pointer">
                                <input type="checkbox" checked={enhance} disabled={busy}
                                    onChange={e => setEnhance(e.target.checked)} />
                                Plan the build first (recommended)
                            </label>
                        </div>
                        <span className="text-xs text-dim">
                            It writes the whole game, then tells you when it can be played.
                        </span>
                    </>
                )}

                {error && <p className="text-fail text-sm">{error}</p>}

                {!reviewing && (
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
                )}
            </div>

            {notice && (
                <EnhanceNotice
                    onEnhance={() => { setNotice(false); void plan() }}
                    onBuildAnyway={() => { setNotice(false); void createPlain() }}
                    onClose={() => setNotice(false)}
                />
            )}
        </div>
    )
}
