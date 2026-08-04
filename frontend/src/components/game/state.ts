export type Stage = 'building' | 'built' | 'ready' | 'held'

export const formatElapsed = (secs: number): string => {
    const s = Math.max(0, Math.floor(secs))
    return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}`
}

export const stageFor = (building: boolean, built: boolean): Stage =>
    building ? 'building' : built ? 'built' : 'ready'

// Whether a freshly loaded prompt replaces what is in the box. `seen` is the text the last load
// carried (null before the first one), so the first load always fills the box and a poll that
// returns the same text leaves an in-progress edit alone.
export const shouldAdoptPrompt = (seen: string | null, incoming: string): boolean =>
    seen !== incoming

// The compute budget as a fraction, 0..1 — users see a bar, never seconds. null ⇒ uncharged, no
// bar. Tolerates the backend sending either a 0..1 fraction or a 0..100 percentage.
export const budgetFraction = (pct: number | null | undefined): number | null => {
    if (pct == null) return null
    return Math.max(0, Math.min(1, pct > 1 ? pct / 100 : pct))
}
