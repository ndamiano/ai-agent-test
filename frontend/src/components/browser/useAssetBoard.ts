import { useCallback, useEffect, useState } from 'react'
import { api } from '../../api/client'
import { useWebSocket } from '../../contexts/WebSocketContext'
import type { Asset, WebSocketMessage } from '../../types'
import { type Board, patchAsset, markDirtyByIdkeys } from './boardUtils'

// D8 — fetches every present component's asset list once, then keeps the board live over the
// existing WebSocket: asset_dirty_set/cleared patch in place, asset_updated re-fetches that one
// item's content. Build lifecycle events are untouched — this hook only reacts to asset_* events.
export function useAssetBoard(runId: string, componentIds: string[]) {
    const { subscribe } = useWebSocket()
    const [board, setBoard] = useState<Board>({})
    const [loading, setLoading] = useState(false)
    const componentIdsKey = componentIds.join(',')

    const reload = useCallback(() => {
        setLoading(true)
        Promise.all(componentIds.map(id => api.listAssets(runId, id).then(assets => [id, assets] as const)))
            .then(results => setBoard(Object.fromEntries(results)))
            .finally(() => setLoading(false))
        // componentIds itself is intentionally omitted — componentIdsKey is the stable proxy for it.
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [runId, componentIdsKey])

    useEffect(() => { reload() }, [reload])

    const refetchOne = useCallback((component: string, itemId: string) => {
        api.getAsset(runId, component, itemId).then(asset => {
            setBoard(prev => {
                const list = prev[component] ?? []
                const idx = list.findIndex(a => a.id === itemId)
                const next = [...list]
                if (idx === -1) next.push(asset); else next[idx] = asset
                return { ...prev, [component]: next }
            })
        }).catch(() => { /* the item may have been removed mid-build; next reload settles it */ })
    }, [runId])

    useEffect(() => {
        const unsub = subscribe(runId, (msg: WebSocketMessage) => {
            if (!msg.component || !msg.item_id) return
            if (msg.type === 'asset_dirty_set') {
                setBoard(prev => patchAsset(prev, msg.component!, msg.item_id!, { dirty: true, review_note: msg.note ?? '' }))
            } else if (msg.type === 'asset_dirty_cleared') {
                setBoard(prev => patchAsset(prev, msg.component!, msg.item_id!, { dirty: false, review_note: '' }))
            } else if (msg.type === 'asset_updated') {
                refetchOne(msg.component, msg.item_id)
            }
        })
        return unsub
    }, [runId, subscribe, refetchOne])

    const applyPatch = useCallback((component: string, itemId: string, patch: Partial<Asset>) => {
        setBoard(prev => patchAsset(prev, component, itemId, patch))
    }, [])

    const markDirty = useCallback((idkeys: string[]) => {
        setBoard(prev => markDirtyByIdkeys(prev, idkeys))
    }, [])

    return { board, loading, reload, applyPatch, markDirty }
}
