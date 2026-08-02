import React from 'react'
import type { GameAsset } from '../../types'
import type { FeedEntry } from '../../hooks/useRunBuildStream'
import { Meter } from '../ui/Meter'
import { SectionLabel } from '../ui/Field'
import { AssetStrip } from './AssetStrip'
import { BuildLog } from './BuildLog'
import { formatElapsed } from './state'

const Counter: React.FC<{ label: string; value: string }> = ({ label, value }) => (
    <div className="flex flex-col">
        <span className="text-lg font-semibold tabular-nums leading-tight">{value}</span>
        <span className="text-xs font-mono text-dim">{label}</span>
    </div>
)

// What is happening right now, in one line. There is no progress bar on purpose: a build ends when
// the model calls done, so a bar would count down to a number nobody has.
export const WorkingPanel: React.FC<{
    runId: string
    paused: boolean
    step: number | null
    summary: string | null
    elapsedSec: number
    assets: GameAsset[] | null
    assetsVersion: number
    budget: number | null
    prompt: string
    feed: FeedEntry[]
}> = ({ runId, paused, step, summary, elapsedSec, assets, assetsVersion, budget, prompt, feed }) => {
    const ready = assets?.filter(a => a.status === 'ready').length ?? 0

    return (
        <div className="grid gap-6 lg:grid-cols-[minmax(0,1.4fr)_minmax(0,1fr)]">
            <div className="flex flex-col gap-5 min-w-0">
                <div className="flex items-start gap-3">
                    <span className={`w-2.5 h-2.5 rounded-full mt-1.5 shrink-0 bg-ember ${paused ? 'opacity-40' : 'animate-pulse'}`} />
                    <p className="text-[15px] leading-relaxed">
                        {paused ? 'The ritual is paused. Nothing is running.' : summary || 'Drawing the circle…'}
                    </p>
                </div>

                <div className="flex gap-8">
                    <Counter label="step" value={step == null ? '—' : String(step)} />
                    <Counter label="running" value={formatElapsed(elapsedSec)} />
                    {assets && assets.length > 0 && (
                        <Counter label="art" value={`${ready} of ${assets.length}`} />
                    )}
                </div>

                <BuildLog feed={feed} />
            </div>

            <div className="flex flex-col gap-6 min-w-0">
                {assets && assets.length > 0 && (
                    <section className="flex flex-col gap-2">
                        <SectionLabel>Art it asked for</SectionLabel>
                        <AssetStrip runId={runId} assets={assets} version={assetsVersion} />
                        <span className="text-xs text-dim">The game draws its own shapes until each one lands.</span>
                    </section>
                )}

                {budget != null && (
                    <section className="flex flex-col gap-2">
                        <Meter value={budget} label="Mana left" />
                        <span className="text-xs text-dim">The mana this summoning has left to spend.</span>
                    </section>
                )}

                <section className="flex flex-col gap-2">
                    <SectionLabel>The request</SectionLabel>
                    <p className="text-sm text-slate leading-relaxed whitespace-pre-wrap">{prompt}</p>
                </section>
            </div>
        </div>
    )
}
