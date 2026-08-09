import React, { useCallback, useEffect, useMemo, useState } from 'react'
import { api } from '../../api/client'
import type { Grade, GradeClaim, GradeTarget } from '../../types'
import { Button } from '../ui/Button'

// docs/game_rubric.md is the form and the reasoning.
//
// Steps advance and do NOT go back, which is the instrument rather than a UI shortcut: a first
// impression that can be rewritten after scoring the dimensions is not a first impression.

const DIMENSIONS: { key: string; label: string; hint: string }[] = [
    { key: 'core_loop', label: 'core loop', hint: 'a thing to do that leads to doing it again, and builds' },
    { key: 'moment_to_moment', label: 'moment-to-moment', hint: 'response, weight, timing — what it gives back for an input' },
    { key: 'legibility', label: 'legibility', hint: 'can you tell what is happening and what you control' },
    { key: 'interface', label: 'interface', hint: 'layout, controls, readability — is what you need to decide on screen when you need it' },
    { key: 'depth', label: 'depth', hint: 'how much game before you have seen all of it (~20 min is the floor)' },
    { key: 'stakes', label: 'stakes', hint: 'can things go wrong, does it land — harshness is not the scale' },
    { key: 'visual_coherence', label: 'visual coherence', hint: 'does it look like one thing made on purpose' },
    { key: 'art_integration', label: 'art integration', hint: 'is generated art load-bearing, or decoration beside primitives' },
    { key: 'sound', label: 'sound', hint: 'any, responsive, fitting' },
    { key: 'character', label: 'character', hint: 'anything memorable — a voice, a joke, one moment worth describing' },
]

const SHOW_SOMEONE = ['no', 'with caveats', 'yes', 'yes and I would point at it']

type Step = 'play' | 'first' | 'verdict' | 'dimensions' | 'considered' | 'fidelity' | 'done'

const STEPS: Step[] = ['play', 'first', 'verdict', 'dimensions', 'considered', 'fidelity', 'done']

const emptyGrade = (): Grade => ({
    loads: true, takes_input: true, crashed: false, crash_note: '',
    first_impression: null, first_impression_note: '',
    show_someone: '', what_is_it: '', biggest_gap: '',
    dimensions: {}, considered: null, considered_note: '', what_moved_it: '',
    claims: [], unrequested: '', play_ended: '', play_minutes: null,
})

const Field: React.FC<{ label: string; hint?: string; children: React.ReactNode }> = ({ label, hint, children }) => (
    <label className="block">
        <span className="text-sm text-bone">{label}</span>
        {hint && <span className="block text-xs text-slate mb-1">{hint}</span>}
        {children}
    </label>
)

const Note: React.FC<{ value: string; onChange: (v: string) => void; placeholder?: string; rows?: number }> = ({
    value, onChange, placeholder, rows = 2,
}) => (
    <textarea value={value} rows={rows} placeholder={placeholder}
        onChange={e => onChange(e.target.value)}
        className="w-full mt-1 rounded border border-edge bg-sunken px-2 py-1.5 text-sm text-bone
                   placeholder:text-slate/60 focus:border-ember focus:outline-none" />
)

// n/a is not a zero and never counts against the game — a dimension the game was never trying for
// gets marked, not scored low.
const Scale: React.FC<{
    value: number | null
    na: boolean
    onScore: (n: number | null) => void
    allowNa?: boolean
}> = ({ value, na, onScore, allowNa = true }) => (
    <div className="flex flex-wrap gap-1 mt-1">
        {Array.from({ length: 10 }, (_, i) => i + 1).map(n => (
            <button key={n} type="button" onClick={() => onScore(n)}
                className={`w-8 h-8 rounded text-sm transition-colors ${
                    !na && value === n ? 'bg-ember text-ink' : 'bg-sunken text-slate hover:text-bone border border-edge'
                }`}>
                {n}
            </button>
        ))}
        {allowNa && (
            <button type="button" onClick={() => onScore(null)}
                title="not what this game is for — not a zero"
                className={`px-2 h-8 rounded text-sm transition-colors ${
                    na ? 'bg-bone/20 text-bone' : 'bg-sunken text-slate hover:text-bone border border-edge'
                }`}>
                n/a
            </button>
        )}
    </div>
)

export const GradePage: React.FC<{ runId: string }> = ({ runId }) => {
    const [target, setTarget] = useState<GradeTarget | null>(null)
    const [sessionUrl, setSessionUrl] = useState<string | null>(null)
    const [step, setStep] = useState<Step>('play')
    const [grade, setGrade] = useState<Grade>(emptyGrade)
    const [naDims, setNaDims] = useState<Record<string, boolean>>({})
    const [error, setError] = useState<string | null>(null)
    const [saved, setSaved] = useState<string | null>(null)
    const frameRef = React.useRef<HTMLIFrameElement>(null)

    useEffect(() => {
        api.gradeTarget(runId).then(setTarget).catch(e => setError(String(e.message ?? e)))
    }, [runId])

    const play = useCallback(async () => {
        try {
            setSessionUrl((await api.playSession(runId)).url)
        } catch (e: any) {
            setError(String(e.message ?? e))
        }
    }, [runId])

    const set = <K extends keyof Grade>(key: K, value: Grade[K]) =>
        setGrade(g => ({ ...g, [key]: value }))

    const setDim = (key: string, score: number | null, note?: string) => {
        setNaDims(n => ({ ...n, [key]: score === null }))
        setGrade(g => ({
            ...g,
            dimensions: {
                ...g.dimensions,
                [key]: { score, note: note ?? g.dimensions[key]?.note ?? '' },
            },
        }))
    }

    const advance = () => setStep(s => STEPS[Math.min(STEPS.indexOf(s) + 1, STEPS.length - 1)])

    const submit = async () => {
        try {
            const res = await api.submitGrade(runId, grade)
            setSaved(res.saved)
            setStep('done')
        } catch (e: any) {
            setError(String(e.message ?? e))
        }
    }

    const addClaim = () =>
        set('claims', [...grade.claims, { claim: '', verdict: 'delivered', note: '' } as GradeClaim])

    const setClaim = (i: number, patch: Partial<GradeClaim>) =>
        set('claims', grade.claims.map((c, j) => (j === i ? { ...c, ...patch } : c)))

    const stepIndex = useMemo(() => STEPS.indexOf(step), [step])

    if (error) return <div className="p-6 text-sm text-ember">{error}</div>
    if (!target) return <div className="p-6 text-sm text-slate">loading…</div>

    return (
        <div className="h-full overflow-y-auto">
            <div className="max-w-3xl mx-auto p-4 space-y-4">
                {/* The game, always on screen. Nothing about the arm appears anywhere on this page. */}
                <div className="relative aspect-video rounded-md border border-edge bg-sunken overflow-hidden grid place-items-center">
                    {sessionUrl ? (
                        <>
                            <iframe ref={frameRef} src={sessionUrl} title="the game"
                                allow="fullscreen; gamepad; autoplay"
                                className="absolute inset-0 w-full h-full border-0" />
                            <button onClick={() => frameRef.current?.requestFullscreen()}
                                title="fullscreen"
                                className="absolute top-2 right-2 px-2 py-1 rounded bg-ink/70 text-bone/80
                                           hover:text-bone text-sm transition-colors">⛶</button>
                        </>
                    ) : (
                        <Button onClick={play}>Play</Button>
                    )}
                </div>

                <div className="flex items-center justify-between text-xs text-slate">
                    <span>grading {runId}</span>
                    <span>
                        {target.previous > 0 && `${target.previous} earlier grade${target.previous > 1 ? 's' : ''} · `}
                        step {stepIndex + 1} of {STEPS.length}
                    </span>
                </div>

                {step === 'play' && (
                    <section className="space-y-3">
                        <p className="text-sm text-slate">
                            Play for 10 minutes, or until you have clearly seen everything. Don't read
                            anything about the build — the request comes at the end.
                        </p>
                        <Field label="did it load and take input?">
                            <div className="flex gap-4 mt-1 text-sm">
                                <label className="flex items-center gap-2 text-bone">
                                    <input type="checkbox" checked={grade.loads}
                                        onChange={e => set('loads', e.target.checked)} /> loads
                                </label>
                                <label className="flex items-center gap-2 text-bone">
                                    <input type="checkbox" checked={grade.takes_input}
                                        onChange={e => set('takes_input', e.target.checked)} /> takes input
                                </label>
                                <label className="flex items-center gap-2 text-bone">
                                    <input type="checkbox" checked={grade.crashed}
                                        onChange={e => set('crashed', e.target.checked)} /> crashed during play
                                </label>
                            </div>
                        </Field>
                        {grade.crashed && (
                            <Field label="where did it go, and what was reachable first?"
                                hint="a crash is a fact about the build, not a reason to stop grading">
                                <Note value={grade.crash_note} onChange={v => set('crash_note', v)} />
                            </Field>
                        )}
                        <Field label="how did play end?">
                            <div className="flex gap-2 mt-1">
                                {['time-box', 'saw everything'].map(v => (
                                    <button key={v} type="button" onClick={() => set('play_ended', v)}
                                        className={`px-3 py-1 rounded text-sm border transition-colors ${
                                            grade.play_ended === v
                                                ? 'bg-ember text-ink border-ember'
                                                : 'bg-sunken text-slate border-edge hover:text-bone'
                                        }`}>{v}</button>
                                ))}
                                <input type="number" min={0} placeholder="min"
                                    value={grade.play_minutes ?? ''}
                                    onChange={e => set('play_minutes', e.target.value === '' ? null : Number(e.target.value))}
                                    className="w-20 rounded border border-edge bg-sunken px-2 text-sm text-bone" />
                            </div>
                        </Field>
                        <Button onClick={advance} disabled={!grade.loads || !grade.takes_input}>
                            Done playing
                        </Button>
                        {(!grade.loads || !grade.takes_input) && (
                            <p className="text-xs text-slate">
                                A game that never opens or never responds isn't gradeable — record it and stop.
                            </p>
                        )}
                    </section>
                )}

                {step === 'first' && (
                    <section className="space-y-3">
                        <h2 className="font-display text-bone">First impression</h2>
                        <p className="text-sm text-slate">Right now, without thinking about it.</p>
                        <Scale value={grade.first_impression} na={false} allowNa={false}
                            onScore={n => set('first_impression', n)} />
                        <Note value={grade.first_impression_note}
                            onChange={v => set('first_impression_note', v)} placeholder="why that number" />
                        <Button onClick={advance} disabled={grade.first_impression === null}>Next</Button>
                    </section>
                )}

                {step === 'verdict' && (
                    <section className="space-y-3">
                        <h2 className="font-display text-bone">The verdict</h2>
                        <Field label="Would I show this to someone whose opinion I care about, without apologising for it first?">
                            <div className="flex flex-wrap gap-2 mt-1">
                                {SHOW_SOMEONE.map(v => (
                                    <button key={v} type="button" onClick={() => set('show_someone', v)}
                                        className={`px-3 py-1 rounded text-sm border transition-colors ${
                                            grade.show_someone === v
                                                ? 'bg-ember text-ink border-ember'
                                                : 'bg-sunken text-slate border-edge hover:text-bone'
                                        }`}>{v}</button>
                                ))}
                            </div>
                        </Field>
                        <Field label="What IS this game, and what did it feel like to play?">
                            <Note value={grade.what_is_it} onChange={v => set('what_is_it', v)} rows={3} />
                        </Field>
                        <Field label="The single biggest thing between this and a game someone would choose to play">
                            <Note value={grade.biggest_gap} onChange={v => set('biggest_gap', v)} />
                        </Field>
                        <Button onClick={advance} disabled={!grade.show_someone}>Next</Button>
                    </section>
                )}

                {step === 'dimensions' && (
                    <section className="space-y-4">
                        <h2 className="font-display text-bone">Dimensions</h2>
                        <p className="text-sm text-slate">
                            Judge the game as it is. These never add up to anything, and a low score is
                            not automatically a defect — mark <span className="text-bone">n/a</span> when
                            the dimension isn't what this game is for.
                        </p>
                        {DIMENSIONS.map(d => (
                            <div key={d.key} className="border-t border-edge pt-3">
                                <Field label={d.label} hint={d.hint}>
                                    <Scale value={grade.dimensions[d.key]?.score ?? null}
                                        na={!!naDims[d.key]}
                                        onScore={n => setDim(d.key, n)} />
                                </Field>
                                <Note value={grade.dimensions[d.key]?.note ?? ''}
                                    onChange={v => setDim(d.key, grade.dimensions[d.key]?.score ?? null, v)}
                                    placeholder="why" />
                            </div>
                        ))}
                        <Button onClick={advance}>Next</Button>
                    </section>
                )}

                {step === 'considered' && (
                    <section className="space-y-3">
                        <h2 className="font-display text-bone">Considered verdict</h2>
                        <p className="text-sm text-slate">
                            Having worked through it. First impression was{' '}
                            <span className="text-bone">{grade.first_impression}</span>.
                        </p>
                        <Scale value={grade.considered} na={false} allowNa={false}
                            onScore={n => set('considered', n)} />
                        <Note value={grade.considered_note} onChange={v => set('considered_note', v)}
                            placeholder="why that number" />
                        {grade.considered !== null && grade.considered !== grade.first_impression && (
                            <Field label="what moved it?">
                                <Note value={grade.what_moved_it} onChange={v => set('what_moved_it', v)} />
                            </Field>
                        )}
                        <Button onClick={advance} disabled={grade.considered === null}>
                            Reveal the request
                        </Button>
                    </section>
                )}

                {step === 'fidelity' && (
                    <section className="space-y-3">
                        <h2 className="font-display text-bone">What was asked for</h2>
                        <pre className="whitespace-pre-wrap rounded border border-edge bg-sunken p-3 text-sm text-bone">
                            {target.request}
                        </pre>
                        <p className="text-sm text-slate">
                            List what the request named, and tick each from what you saw. Judge as the
                            player you were — a system you'd never have found is <em>absent</em>.
                        </p>
                        {grade.claims.map((c, i) => (
                            <div key={i} className="flex flex-wrap gap-2 items-start">
                                <input value={c.claim} placeholder="what was asked for"
                                    onChange={e => setClaim(i, { claim: e.target.value })}
                                    className="flex-1 min-w-[12rem] rounded border border-edge bg-sunken px-2 py-1 text-sm text-bone" />
                                <select value={c.verdict}
                                    onChange={e => setClaim(i, { verdict: e.target.value as GradeClaim['verdict'] })}
                                    className="rounded border border-edge bg-sunken px-2 py-1 text-sm text-bone">
                                    <option value="delivered">delivered</option>
                                    <option value="partial">partial</option>
                                    <option value="absent">absent</option>
                                </select>
                                <input value={c.note} placeholder="note"
                                    onChange={e => setClaim(i, { note: e.target.value })}
                                    className="flex-1 min-w-[10rem] rounded border border-edge bg-sunken px-2 py-1 text-sm text-bone" />
                            </div>
                        ))}
                        <Button variant="ghost" onClick={addClaim}>+ claim</Button>
                        <Field label="anything present that was never asked for?">
                            <Note value={grade.unrequested} onChange={v => set('unrequested', v)} />
                        </Field>
                        <Button onClick={submit}>Save grade</Button>
                    </section>
                )}

                {step === 'done' && (
                    <section className="space-y-2">
                        <h2 className="font-display text-bone">Saved</h2>
                        <p className="text-sm text-slate">{saved}</p>
                    </section>
                )}
            </div>
        </div>
    )
}

export default GradePage
