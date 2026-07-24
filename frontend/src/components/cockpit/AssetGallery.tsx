import React, { useEffect, useState } from 'react'
import { api } from '../../api/client'
import type { GameAsset } from '../../types'

type RegenMode = 'full' | 'img2img'

// A transparent-sprite-friendly checkerboard so a white or partially-transparent PNG is still
// visible against the dark UI — the whole point is to judge whether the art rendered well.
const CHECKER: React.CSSProperties = {
    backgroundImage:
        'linear-gradient(45deg,#2a2a2a 25%,transparent 25%),linear-gradient(-45deg,#2a2a2a 25%,transparent 25%),' +
        'linear-gradient(45deg,transparent 75%,#2a2a2a 75%),linear-gradient(-45deg,transparent 75%,#2a2a2a 75%)',
    backgroundSize: '14px 14px',
    backgroundPosition: '0 0,0 7px,7px -7px,-7px 0',
}

// The bytes come from the authed blob route, so <img> can't load them by URL — fetch to an object
// URL and revoke it on unmount / when the asset changes. `version` is in the deps so a regenerated
// asset (whose status stays 'ready' across the swap) still refetches its new bytes on assets_done.
const useAssetBlob = (runId: string, asset: GameAsset, version: number): string | null => {
    const [url, setUrl] = useState<string | null>(null)
    useEffect(() => {
        if (asset.status !== 'ready') { setUrl(null); return }
        let live = true
        let made: string | null = null
        api.getAssetBlobUrl(runId, asset.id)
            .then(u => { if (live) { made = u; setUrl(u) } else URL.revokeObjectURL(u) })
            .catch(() => { if (live) setUrl(null) })
        return () => { live = false; if (made) URL.revokeObjectURL(made) }
    }, [runId, asset.id, asset.status, version])
    return url
}

const StatusNote: React.FC<{ status: GameAsset['status'] }> = ({ status }) => (
    <span className="text-[10px] text-gray-500 flex items-center gap-1.5">
        {status === 'rendering' && <span className="inline-block w-1.5 h-1.5 rounded-full bg-amber-400 animate-pulse" />}
        {status === 'rendering' ? 'rendering…' : 'pending'}
    </span>
)

// Every card carries a ↻ that reveals a prompt input; submitting re-renders just this asset.
// "redraw" renders from scratch; "refine" is img2img off the current image, keeping its composition.
const CardShell: React.FC<{
    id: string
    status: GameAsset['status']
    right?: React.ReactNode
    onRegenerate: (prompt: string, mode: RegenMode) => void
    children: React.ReactNode
}> = ({ id, status, right, onRegenerate, children }) => {
    const [open, setOpen] = useState(false)
    const [prompt, setPrompt] = useState('')
    const [mode, setMode] = useState<RegenMode>('full')
    const submit = () => {
        const p = prompt.trim()
        if (!p) return
        onRegenerate(p, mode)
        setPrompt(''); setOpen(false)
    }
    return (
        <div className="rounded-lg border border-white/[0.06] overflow-hidden bg-[#141414]">
            <div className="aspect-square flex items-center justify-center p-3" style={CHECKER}>{children}</div>
            <div className="px-2 py-1.5 flex items-center justify-between gap-2">
                <span className="text-gray-300 text-[11px] font-medium truncate" title={id}>{id}</span>
                <div className="flex items-center gap-2 shrink-0">
                    {right}
                    <button onClick={() => setOpen(o => !o)} disabled={status === 'rendering'}
                        title="regenerate this asset"
                        className="text-gray-400 hover:text-gray-200 disabled:opacity-30 text-xs leading-none">↻</button>
                </div>
            </div>
            {open && (
                <div className="px-2 pb-2 space-y-1">
                    <div className="flex gap-1">
                        {(['full', 'img2img'] as const).map(m => (
                            <button key={m} onClick={() => setMode(m)}
                                title={m === 'full' ? 'render from scratch' : 'refine the current image (keeps its composition)'}
                                className={`px-1.5 py-0.5 rounded text-[10px] ${mode === m
                                    ? 'bg-white/[0.14] text-gray-200'
                                    : 'bg-black/40 text-gray-500 hover:text-gray-300'}`}>
                                {m === 'full' ? 'redraw' : 'refine'}
                            </button>
                        ))}
                    </div>
                    <div className="flex gap-1">
                        <input autoFocus value={prompt} onChange={e => setPrompt(e.target.value)}
                            onKeyDown={e => { if (e.key === 'Enter') submit(); else if (e.key === 'Escape') setOpen(false) }}
                            placeholder="what should change…"
                            className="flex-1 min-w-0 bg-black/40 border border-white/[0.1] rounded text-[11px] text-gray-200 px-1.5 py-1" />
                        <button onClick={submit} disabled={!prompt.trim()}
                            className="bg-blue-600/80 hover:bg-blue-700 disabled:opacity-40 text-white px-2 rounded text-[11px] shrink-0">Go</button>
                    </div>
                </div>
            )}
        </div>
    )
}

const SpriteCard: React.FC<{
    runId: string; asset: GameAsset; version: number; regenerating: boolean
    onRegenerate: (prompt: string, mode: RegenMode) => void
}> = ({ runId, asset, version, regenerating, onRegenerate }) => {
    const status = regenerating ? 'rendering' : asset.status
    const url = useAssetBlob(runId, asset, version)
    return (
        <CardShell id={asset.id} status={status} onRegenerate={onRegenerate}
            right={asset.w && asset.h ? <span className="text-gray-600 text-[10px] font-mono shrink-0">{asset.w}×{asset.h}</span> : undefined}>
            {url && status === 'ready'
                ? <img src={url} alt={asset.id} className="max-w-full max-h-full object-contain [image-rendering:pixelated]" />
                : <StatusNote status={status} />}
        </CardShell>
    )
}

const MeshCard: React.FC<{
    runId: string; asset: GameAsset; regenerating: boolean
    onRegenerate: (prompt: string, mode: RegenMode) => void
}> = ({ runId, asset, regenerating, onRegenerate }) => {
    const status = regenerating ? 'rendering' : asset.status
    const [busy, setBusy] = useState(false)
    const download = async () => {
        setBusy(true)
        try {
            const u = await api.getAssetBlobUrl(runId, asset.id)
            const a = document.createElement('a')
            a.href = u; a.download = `${asset.id}.glb`; a.click()
            URL.revokeObjectURL(u)
        } catch { /* transient — the button stays clickable */ }
        finally { setBusy(false) }
    }
    return (
        <CardShell id={asset.id} status={status} onRegenerate={onRegenerate}
            right={status === 'ready'
                ? <button onClick={download} disabled={busy}
                    className="text-blue-400 hover:text-blue-300 disabled:opacity-40 text-[10px] shrink-0">GLB ↓</button>
                : undefined}>
            {status === 'ready' ? <span className="text-4xl opacity-40">⬡</span> : <StatusNote status={status} />}
        </CardShell>
    )
}

// `version` bumps when a skin/regen run finishes (assets_done) so the gallery re-reads the manifest
// AND every ready card refetches its bytes — a regenerated file swaps under an unchanged id/status.
export const AssetGallery: React.FC<{
    runId: string
    version: number
    skinning: boolean
    canSkin: boolean
    onSkin: () => void
    acting: boolean
}> = ({ runId, version, skinning, canSkin, onSkin, acting }) => {
    const [assets, setAssets] = useState<GameAsset[] | null>(null)
    // Assets the user just asked to regenerate — shown as 'rendering' until the next assets_done
    // (version bump) clears the optimism and the refetched manifest + bytes take over.
    const [regenerating, setRegenerating] = useState<Set<string>>(new Set())

    useEffect(() => {
        let cancelled = false
        setAssets(null)
        api.getGameAssets(runId).then(a => { if (!cancelled) setAssets(a) }).catch(() => { if (!cancelled) setAssets([]) })
        return () => { cancelled = true }
    }, [runId, version])

    useEffect(() => { setRegenerating(new Set()) }, [runId, version])

    const regenerate = (asset: GameAsset, prompt: string, mode: RegenMode) => {
        setRegenerating(prev => new Set(prev).add(asset.id))
        api.regenerateAsset(runId, asset.id, prompt, mode).catch(() => {
            setRegenerating(prev => { const next = new Set(prev); next.delete(asset.id); return next })
        })
    }

    const has = assets && assets.length > 0

    return (
        <section className="space-y-2.5">
            <div className="flex items-center gap-2">
                <h3 className="text-gray-300 text-xs font-semibold uppercase tracking-wide">Assets</h3>
                {has && <span className="text-gray-600 text-[11px]">{assets!.length}</span>}
                {canSkin && (
                    <button onClick={onSkin} disabled={acting || skinning}
                        title={has ? 're-plan + re-render all assets' : 'plan + render assets for this game'}
                        className="ml-auto bg-white/[0.08] hover:bg-white/[0.14] disabled:opacity-40 text-gray-200 px-2.5 py-1 rounded text-[11px] font-medium">
                        {skinning ? 'Skinning…' : has ? 'Re-skin' : 'Skin assets'}
                    </button>
                )}
            </div>

            {skinning && (
                <div className="text-amber-400 text-xs flex items-center gap-2">
                    <span className="inline-block w-1.5 h-1.5 rounded-full bg-amber-400 animate-pulse" />
                    rendering assets on the GPU…
                </div>
            )}

            {assets === null ? (
                <div className="text-gray-600 text-xs">loading assets…</div>
            ) : !has ? (
                <div className="text-gray-600 text-xs italic">
                    {skinning ? 'No assets yet — first renders will appear here.' : 'This game renders as shapes — Skin it to generate art.'}
                </div>
            ) : (
                <div className="grid grid-cols-2 sm:grid-cols-3 md:grid-cols-4 gap-2.5">
                    {assets!.map(a => a.kind === 'sprite'
                        ? <SpriteCard key={a.id} runId={runId} asset={a} version={version}
                            regenerating={regenerating.has(a.id)} onRegenerate={(p, m) => regenerate(a, p, m)} />
                        : <MeshCard key={a.id} runId={runId} asset={a}
                            regenerating={regenerating.has(a.id)} onRegenerate={(p, m) => regenerate(a, p, m)} />)}
                </div>
            )}
        </section>
    )
}
