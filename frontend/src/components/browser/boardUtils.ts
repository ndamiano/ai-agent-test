import type { Asset } from '../../types'

// component_id -> its assets, in listAssets order. The board is the browser's whole live state.
export type Board = Record<string, Asset[]>

export function splitIdkey(idkey: string): [component: string, itemId: string] {
    const i = idkey.indexOf(':')
    return i === -1 ? [idkey, idkey] : [idkey.slice(0, i), idkey.slice(i + 1)]
}

export function patchAsset(board: Board, component: string, itemId: string, patch: Partial<Asset>): Board {
    const list = board[component]
    if (!list) return board
    const idx = list.findIndex(a => a.id === itemId)
    if (idx === -1) return board
    const next = [...list]
    next[idx] = { ...next[idx], ...patch }
    return { ...board, [component]: next }
}

export function markDirtyByIdkeys(board: Board, idkeys: string[]): Board {
    return idkeys.reduce((b, idkey) => {
        const [component, itemId] = splitIdkey(idkey)
        return patchAsset(b, component, itemId, { dirty: true })
    }, board)
}

// Per-tab dirty badge counts (D7) — every component id gets an entry, 0 if unloaded/clean.
export function computeDirtyCounts(board: Board, componentIds: string[]): Record<string, number> {
    return Object.fromEntries(componentIds.map(id => [id, (board[id] ?? []).filter(a => a.dirty).length]))
}
