import React, { useEffect, useState } from 'react'
import { api } from '../../api/client'
import type { Asset } from '../../types'
import Tabs from './Tabs'
import NavStrip from './NavStrip'
import AssetCard from './AssetCard'
import { useAssetBoard } from './useAssetBoard'
import { computeDirtyCounts } from './boardUtils'

// D1 + D2 — the modular component browser shell: an optional leading "Spec" tab, then one tab per
// component type actually present in the run, a central card for the selected asset, and
// prev/next/jump-to-dirty navigation (mouse + keyboard ←/→) within the active tab. Component-blind:
// the registry (registry.ts) is the only component-aware seam (D3).
const ComponentBrowser: React.FC<{
    runId: string
    componentIds: string[]
    editable: boolean
    specNode?: React.ReactNode
}> = ({ runId, componentIds, editable, specNode }) => {
    const { board, loading, applyPatch, markDirty } = useAssetBoard(runId, componentIds)
    const hasSpec = !!specNode
    const tabIds = hasSpec ? ['spec', ...componentIds] : componentIds
    const [active, setActive] = useState(componentIds[0] ?? (hasSpec ? 'spec' : ''))
    const [indexByComponent, setIndexByComponent] = useState<Record<string, number>>({})
    const [busyIdkey, setBusyIdkey] = useState<string | null>(null)
    const [err, setErr] = useState<string | null>(null)

    useEffect(() => {
        if (!tabIds.includes(active)) setActive(componentIds[0] ?? (hasSpec ? 'spec' : ''))
        // tabIds is derived from componentIds + hasSpec, so it's covered by the listed deps.
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [componentIds, active, hasSpec])

    const assets = board[active] ?? []
    const activeIndex = Math.min(indexByComponent[active] ?? 0, Math.max(0, assets.length - 1))
    const setActiveIndex = (i: number) => setIndexByComponent(prev => ({ ...prev, [active]: i }))
    const asset: Asset | undefined = assets[activeIndex]

    // Keyboard ←/→ move within the active component — ignored while editing a field or on the spec tab.
    useEffect(() => {
        const onKey = (e: KeyboardEvent) => {
            if (e.key !== 'ArrowLeft' && e.key !== 'ArrowRight') return
            const t = e.target as HTMLElement | null
            if (t && (t.tagName === 'INPUT' || t.tagName === 'TEXTAREA' || t.tagName === 'SELECT' || t.isContentEditable)) return
            if (active === 'spec') return
            const n = (board[active] ?? []).length
            if (n === 0) return
            e.preventDefault()
            const cur = Math.min(indexByComponent[active] ?? 0, n - 1)
            setActiveIndex(e.key === 'ArrowLeft' ? Math.max(0, cur - 1) : Math.min(n - 1, cur + 1))
        }
        window.addEventListener('keydown', onKey)
        return () => window.removeEventListener('keydown', onKey)
        // setActiveIndex is a stable setter wrapper over the listed state.
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [active, board, indexByComponent])

    const dirtyCounts = computeDirtyCounts(board, componentIds)

    const withBusy = async (idkey: string, fn: () => Promise<void>) => {
        setBusyIdkey(idkey); setErr(null)
        try { await fn() }
        catch (e) { setErr(e instanceof Error ? e.message : 'action failed') }
        finally { setBusyIdkey(null) }
    }

    const saveAsset = (a: Asset) => (content: Record<string, unknown>) => withBusy(a.idkey, async () => {
        const res = await api.editAsset(runId, a.component, a.id, content)
        applyPatch(a.component, a.id, { content, dirty: res.cleared_own_dirty ? false : a.dirty })
        markDirty(res.flagged_dependents)
    })

    const thumbsUp = (a: Asset) => () => withBusy(a.idkey, async () => {
        await api.thumbsUpAsset(runId, a.idkey)
        applyPatch(a.component, a.id, { dirty: false, review_note: '' })
    })

    const thumbsDown = (a: Asset) => (note: string) => withBusy(a.idkey, async () => {
        await api.thumbsDownAsset(runId, a.idkey, note)
        applyPatch(a.component, a.id, { dirty: true, review_note: note })
    })

    if (tabIds.length === 0) {
        return <div className="p-6 text-gray-600 text-sm">Nothing here yet — components appear once the build writes something.</div>
    }

    return (
        <div className="space-y-3">
            <Tabs componentIds={tabIds} active={active} dirtyCounts={dirtyCounts} onPick={setActive} />
            {err && <p className="text-red-400 text-xs">{err}</p>}
            {active === 'spec' ? (
                <div className="space-y-6">{specNode}</div>
            ) : loading && Object.keys(board).length === 0 ? (
                <div className="p-2 text-gray-500 text-sm">Loading components…</div>
            ) : assets.length === 0 ? (
                <p className="text-gray-600 text-sm">No items in this component yet.</p>
            ) : (
                <>
                    <NavStrip assets={assets} activeIndex={activeIndex} onPick={setActiveIndex} />
                    {asset && (
                        <AssetCard asset={asset} editable={editable} busy={busyIdkey === asset.idkey}
                            onSave={saveAsset(asset)} onThumbsUp={thumbsUp(asset)} onThumbsDown={thumbsDown(asset)} />
                    )}
                </>
            )}
        </div>
    )
}

export default ComponentBrowser
