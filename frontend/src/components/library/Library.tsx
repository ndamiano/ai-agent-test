import React from 'react'
import type { Game } from '../../types'
import { GameCard } from './GameCard'

const NewGameCard: React.FC<{ onClick: () => void }> = ({ onClick }) => (
    <button onClick={onClick}
        className="border border-dashed border-edge rounded-md min-h-[200px] flex flex-col items-center
                   justify-center gap-2 text-center px-4 hover:border-ember/60 transition-colors">
        <span className="w-5 h-5 rotate-45 border-[1.5px] border-ember" />
        <span className="font-display text-lg">Make a new game</span>
        <span className="text-xs text-dim">Describe it in a sentence</span>
    </button>
)

export const Library: React.FC<{
    games: Game[]
    loading: boolean
    error: string | null
    onOpen: (runId: string) => void
    onNew: () => void
}> = ({ games, loading, error, onOpen, onNew }) => {
    const working = games.filter(g => g.building).length

    return (
        <div className="h-full overflow-y-auto">
            <div className="max-w-6xl mx-auto px-6 py-6 flex flex-col gap-5">
                <div className="flex items-baseline justify-between gap-4">
                    <h2 className="font-display text-2xl">Your games</h2>
                    {games.length > 0 && (
                        <span className="text-xs text-dim font-mono">
                            {games.length} summoned{working > 0 && ` · ${working} summoning`}
                        </span>
                    )}
                </div>

                {error && <p className="text-fail text-sm">{error}</p>}
                {loading && games.length === 0 && <p className="text-slate text-sm">Loading…</p>}

                <div className="grid gap-4 grid-cols-[repeat(auto-fill,minmax(190px,1fr))]">
                    <NewGameCard onClick={onNew} />
                    {games.map(g => <GameCard key={g.run_id} game={g} onOpen={() => onOpen(g.run_id)} />)}
                </div>
            </div>
        </div>
    )
}
