import React, { useEffect, useMemo, useState } from 'react'
import { api } from '../../api/client'
import type { Grade } from '../../types'
import { Link } from '../../router'

// Every grade side by side. A dimension low on one game is noise; the same dimension low across a
// battery is a work item on the loop, and that is only visible with the grades in one place.

type Row = Grade & { run_id: string; graded_at: string; maestro_rev: string }

const DIMS = ['core_loop', 'moment_to_moment', 'legibility', 'interface', 'depth', 'stakes',
              'visual_coherence', 'art_integration', 'sound', 'character'] as const

const SHORT: Record<string, string> = {
    core_loop: 'loop', moment_to_moment: 'feel', legibility: 'legib', interface: 'iface',
    depth: 'depth', stakes: 'stakes', visual_coherence: 'visual', art_integration: 'art',
    sound: 'sound', character: 'char',
}

// n/a is not a zero and must never be averaged as one — it means the dimension was not what the
// game was for, so it leaves the sample rather than dragging it down.
const stats = (rows: Row[], dim: string) => {
    const scored = rows.map(r => r.dimensions?.[dim]?.score).filter((s): s is number => typeof s === 'number')
    const na = rows.length - scored.length
    if (!scored.length) return { median: null, low: 0, n: 0, na }
    const sorted = [...scored].sort((a, b) => a - b)
    const mid = Math.floor(sorted.length / 2)
    const median = sorted.length % 2 ? sorted[mid] : (sorted[mid - 1] + sorted[mid]) / 2
    return { median, low: scored.filter(s => s <= 3).length, n: scored.length, na }
}

const tone = (s: number | null) =>
    s == null ? 'text-slate/40'
        : s <= 3 ? 'text-ember'
        : s <= 6 ? 'text-wait'
        : 'text-mana'

export const GradesPanel: React.FC = () => {
    const [rows, setRows] = useState<Row[] | null>(null)
    const [error, setError] = useState<string | null>(null)
    const [open, setOpen] = useState<string | null>(null)

    useEffect(() => {
        api.allGrades().then(r => setRows(r.grades as Row[])).catch(e => setError(String(e.message ?? e)))
    }, [])

    const summary = useMemo(() => rows ? DIMS.map(d => ({ dim: d, ...stats(rows, d) })) : [], [rows])

    if (error) return <div className="p-6 text-sm text-ember">{error}</div>
    if (!rows) return <div className="p-6 text-sm text-slate">loading…</div>
    if (!rows.length) return (
        <div className="p-6 text-sm text-slate">
            No grades yet. Play a built game and press <span className="text-bone">Grade it</span>.
        </div>
    )

    return (
        <div className="h-full overflow-auto p-4">
            <div className="max-w-6xl mx-auto space-y-8">
                <section>
                    <h2 className="font-display text-bone mb-1">Across {rows.length} grade{rows.length > 1 ? 's' : ''}</h2>
                    <p className="text-xs text-slate mb-3">
                        Median of the scored games. <span className="text-bone">low</span> counts games at 3 or under —
                        that column is where a work item comes from. n/a never counts as a zero.
                    </p>
                    <div className="overflow-x-auto">
                        <table className="text-sm w-full">
                            <thead className="text-slate text-xs">
                                <tr>
                                    <th className="text-left font-normal py-1 pr-4">dimension</th>
                                    <th className="text-right font-normal px-3">median</th>
                                    <th className="text-right font-normal px-3">low (≤3)</th>
                                    <th className="text-right font-normal px-3">scored</th>
                                    <th className="text-right font-normal px-3">n/a</th>
                                </tr>
                            </thead>
                            <tbody>
                                {summary.map(s => (
                                    <tr key={s.dim} className="border-t border-edge">
                                        <td className="py-1 pr-4 text-bone">{s.dim.replace(/_/g, ' ')}</td>
                                        <td className={`text-right px-3 font-mono ${tone(s.median)}`}>
                                            {s.median ?? '—'}
                                        </td>
                                        <td className={`text-right px-3 font-mono ${s.low && s.low === s.n ? 'text-ember' : 'text-slate'}`}>
                                            {s.low}/{s.n}
                                        </td>
                                        <td className="text-right px-3 font-mono text-slate">{s.n}</td>
                                        <td className="text-right px-3 font-mono text-slate/50">{s.na || ''}</td>
                                    </tr>
                                ))}
                            </tbody>
                        </table>
                    </div>
                </section>

                <section>
                    <h2 className="font-display text-bone mb-3">Every grade</h2>
                    <div className="overflow-x-auto">
                        <table className="text-sm w-full">
                            <thead className="text-slate text-xs">
                                <tr>
                                    <th className="text-left font-normal py-1 pr-3">game</th>
                                    <th className="text-left font-normal px-2">built</th>
                                    <th className="text-right font-normal px-2">1st</th>
                                    <th className="text-right font-normal px-2">final</th>
                                    {DIMS.map(d => (
                                        <th key={d} className="text-right font-normal px-1.5" title={d}>{SHORT[d]}</th>
                                    ))}
                                </tr>
                            </thead>
                            <tbody>
                                {rows.map(r => {
                                    const key = `${r.run_id}-${r.graded_at}`
                                    return (
                                        <React.Fragment key={key}>
                                            <tr className="border-t border-edge hover:bg-bone/5 cursor-pointer"
                                                onClick={() => setOpen(open === key ? null : key)}>
                                                <td className="py-1 pr-3">
                                                    <Link to={`/game/${r.run_id}`} onClick={e => e.stopPropagation()}
                                                        className="font-mono text-xs text-slate hover:text-bone">
                                                        {r.run_id}
                                                    </Link>
                                                </td>
                                                <td className="px-2 font-mono text-xs text-slate/70">{r.maestro_rev}</td>
                                                <td className={`text-right px-2 font-mono ${tone(r.first_impression)}`}>
                                                    {r.first_impression ?? '—'}
                                                </td>
                                                <td className={`text-right px-2 font-mono ${tone(r.considered)}`}>
                                                    {r.considered ?? '—'}
                                                </td>
                                                {DIMS.map(d => {
                                                    const s = r.dimensions?.[d]?.score ?? null
                                                    return (
                                                        <td key={d} className={`text-right px-1.5 font-mono ${tone(s)}`}>
                                                            {s ?? 'n/a'}
                                                        </td>
                                                    )
                                                })}
                                            </tr>
                                            {open === key && (
                                                <tr className="border-t border-edge bg-sunken/50">
                                                    <td colSpan={4 + DIMS.length} className="p-3 space-y-2 text-sm">
                                                        <p className="text-bone">{r.what_is_it}</p>
                                                        <p className="text-slate"><span className="text-bone">biggest gap:</span> {r.biggest_gap}</p>
                                                        {DIMS.map(d => r.dimensions?.[d]?.note && (
                                                            <p key={d} className="text-slate text-xs">
                                                                <span className="text-bone">{d.replace(/_/g, ' ')}</span>
                                                                {' '}({r.dimensions[d].score ?? 'n/a'}) — {r.dimensions[d].note}
                                                            </p>
                                                        ))}
                                                        {!!r.claims?.length && (
                                                            <p className="text-xs text-slate">
                                                                <span className="text-bone">claims:</span>{' '}
                                                                {r.claims.filter(c => c.verdict === 'delivered').length}/{r.claims.length} delivered
                                                            </p>
                                                        )}
                                                    </td>
                                                </tr>
                                            )}
                                        </React.Fragment>
                                    )
                                })}
                            </tbody>
                        </table>
                    </div>
                </section>
            </div>
        </div>
    )
}

export default GradesPanel
