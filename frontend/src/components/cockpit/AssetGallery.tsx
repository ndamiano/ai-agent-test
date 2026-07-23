import React, { useEffect, useState } from 'react'
import { api } from '../../api/client'
import type { GameAsset } from '../../types'

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
// URL and revoke it on unmount / when the asset changes. Only fetches once the asset is ready.
const useAssetBlob = (runId: string, asset: GameAsset): string | null => {
    const [url, setUrl] = useState<string | null>(null)
    useEffect(() => {
        if (asset.status !== 'ready') { setUrl(null); return }
        let live = true
        let made: string | null = null
        api.getAssetBlobUrl(runId, asset.id)
            .then(u => { if (live) { made = u; setUrl(u) } else URL.revokeObjectURL(u) })
            .catch(() => { if (live) setUrl(null) })
        return () => { live = false; if (made) URL.revokeObjectURL(made) }
    }, [runId, asset.id, asset.status])
    return url
}

const StatusNote: React.FC<{ status: GameAsset['status'] }> = ({ status }) => (
    <span className="text-[10px] text-gray-500 flex items-center gap-1.5">
        {status === 'rendering' && <span className="inline-block w-1.5 h-1.5 rounded-full bg-amber-400 animate-pulse" />}
        {status === 'rendering' ? 'rendering…' : 'pending'}
    </span>
)

const CardShell: React.FC<{ id: string; right?: React.ReactNode; children: React.ReactNode }> = ({ id, right, children }) => (
    <div className="rounded-lg border border-white/[0.06] overflow-hidden bg-[#141414]">
        <div className="aspect-square flex items-center justify-center p-3" style={CHECKER}>{children}</div>
        <div className="px-2 py-1.5 flex items-center justify-between gap-2">
            <span className="text-gray-300 text-[11px] font-medium truncate" title={id}>{id}</span>
            {right}
        </div>
    </div>
)

const SpriteCard: React.FC<{ runId: string; asset: GameAsset }> = ({ runId, asset }) => {
    const url = useAssetBlob(runId, asset)
    return (
        <CardShell id={asset.id}
            right={asset.w && asset.h ? <span className="text-gray-600 text-[10px] font-mono shrink-0">{asset.w}×{asset.h}</span> : undefined}>
            {url
                ? <img src={url} alt={asset.id} className="max-w-full max-h-full object-contain [image-rendering:pixelated]" />
                : <StatusNote status={asset.status} />}
        </CardShell>
    )
}

const MeshCard: React.FC<{ runId: string; asset: GameAsset }> = ({ runId, asset }) => {
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
        <CardShell id={asset.id}
            right={asset.status === 'ready'
                ? <button onClick={download} disabled={busy}
                    className="text-blue-400 hover:text-blue-300 disabled:opacity-40 text-[10px] shrink-0">GLB ↓</button>
                : undefined}>
            {asset.status === 'ready' ? <span className="text-4xl opacity-40">⬡</span> : <StatusNote status={asset.status} />}
        </CardShell>
    )
}

// `version` bumps when a skin run finishes (assets_done) so the gallery re-reads the manifest.
export const AssetGallery: React.FC<{
    runId: string
    version: number
    skinning: boolean
    canSkin: boolean
    onSkin: () => void
    acting: boolean
}> = ({ runId, version, skinning, canSkin, onSkin, acting }) => {
    const [assets, setAssets] = useState<GameAsset[] | null>(null)

    useEffect(() => {
        let cancelled = false
        setAssets(null)
        api.getGameAssets(runId).then(a => { if (!cancelled) setAssets(a) }).catch(() => { if (!cancelled) setAssets([]) })
        return () => { cancelled = true }
    }, [runId, version])

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
                        ? <SpriteCard key={a.id} runId={runId} asset={a} />
                        : <MeshCard key={a.id} runId={runId} asset={a} />)}
                </div>
            )}
        </section>
    )
}
