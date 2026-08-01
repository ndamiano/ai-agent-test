import React, { useEffect, useState } from 'react'
import { api } from '../../api/client'
import type { GameAsset } from '../../types'
import { Button } from '../ui/Button'
import { SectionLabel, TextInput } from '../ui/Field'
import { useAssetBlob, useAssets } from './useAssets'

type RegenMode = 'full' | 'img2img'

// A transparent-sprite-friendly checkerboard so a white or partially-transparent PNG is still
// visible against the dark UI — the whole point is to judge whether the art rendered well.
const CHECKER: React.CSSProperties = {
    backgroundImage:
        'linear-gradient(45deg,#1b2131 25%,transparent 25%),linear-gradient(-45deg,#1b2131 25%,transparent 25%),' +
        'linear-gradient(45deg,transparent 75%,#1b2131 75%),linear-gradient(-45deg,transparent 75%,#1b2131 75%)',
    backgroundSize: '14px 14px',
    backgroundPosition: '0 0,0 7px,7px -7px,-7px 0',
}

const StatusNote: React.FC<{ status: GameAsset['status'] }> = ({ status }) => (
    <span className="text-xs text-dim flex items-center gap-1.5">
        {status === 'rendering' && <span className="inline-block w-1.5 h-1.5 rounded-full bg-wait animate-pulse" />}
        {status === 'rendering' ? 'drawing' : 'queued'}
    </span>
)

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
        <div className="rounded border border-edge overflow-hidden bg-panel">
            <div className="aspect-square flex items-center justify-center p-3" style={CHECKER}>{children}</div>
            <div className="px-2 py-1.5 flex items-center justify-between gap-2">
                <span className="text-xs truncate" title={id}>{id}</span>
                <div className="flex items-center gap-2 shrink-0">
                    {right}
                    <button onClick={() => setOpen(o => !o)} disabled={status === 'rendering'}
                        title="redraw this one"
                        className="text-slate hover:text-bone disabled:opacity-30 text-sm leading-none">↻</button>
                </div>
            </div>
            {open && (
                <div className="px-2 pb-2 flex flex-col gap-1.5">
                    <div className="flex gap-1">
                        {(['full', 'img2img'] as const).map(m => (
                            <button key={m} onClick={() => setMode(m)}
                                title={m === 'full' ? 'draw it again from scratch' : 'keep the composition, change the details'}
                                className={`px-2 py-0.5 rounded text-xs ${mode === m
                                    ? 'bg-bone/[0.14] text-bone'
                                    : 'bg-sunken text-slate hover:text-bone'}`}>
                                {m === 'full' ? 'redraw' : 'refine'}
                            </button>
                        ))}
                    </div>
                    <div className="flex gap-1.5">
                        <TextInput autoFocus value={prompt} onChange={e => setPrompt(e.target.value)}
                            onKeyDown={e => { if (e.key === 'Enter') submit(); else if (e.key === 'Escape') setOpen(false) }}
                            placeholder="what should change…" className="text-xs px-2 py-1" />
                        <Button variant="primary" onClick={submit} disabled={!prompt.trim()}>Go</Button>
                    </div>
                </div>
            )}
        </div>
    )
}

const MeshDownload: React.FC<{ runId: string; assetId: string }> = ({ runId, assetId }) => {
    const [busy, setBusy] = useState(false)

    const download = async () => {
        setBusy(true)
        try {
            const u = await api.getAssetBlobUrl(runId, assetId)
            const a = document.createElement('a')
            a.href = u; a.download = `${assetId}.glb`; a.click()
            URL.revokeObjectURL(u)
        } catch { /* transient — the button stays clickable */ }
        finally { setBusy(false) }
    }

    return (
        <button onClick={download} disabled={busy}
            className="text-ember hover:text-ember/80 disabled:opacity-40 text-xs shrink-0">GLB ↓</button>
    )
}

const AssetCard: React.FC<{
    runId: string; asset: GameAsset; version: number; regenerating: boolean
    onRegenerate: (prompt: string, mode: RegenMode) => void
}> = ({ runId, asset, version, regenerating, onRegenerate }) => {
    const status = regenerating ? 'rendering' : asset.status
    const isImage = asset.kind === 'image'
    const url = useAssetBlob(runId, regenerating ? null : asset, version)
    const ready = status === 'ready'

    return (
        <CardShell id={asset.id} status={status} onRegenerate={onRegenerate}
            right={!isImage && ready ? <MeshDownload runId={runId} assetId={asset.id} /> : undefined}>
            {!ready ? <StatusNote status={status} />
                : isImage
                    ? url && <img src={url} alt={asset.id} className="max-w-full max-h-full object-contain [image-rendering:pixelated]" />
                    : <span className="text-4xl opacity-40">⬡</span>}
        </CardShell>
    )
}

// `version` bumps when a render/regen run finishes (assets_done) so the gallery re-reads the
// manifest AND every ready card refetches its bytes — a regenerated file swaps under an unchanged
// id and status.
export const AssetGallery: React.FC<{
    runId: string
    version: number
    rendering: boolean
    canRender: boolean
    onRender: () => void
    acting: boolean
}> = ({ runId, version, rendering, canRender, onRender, acting }) => {
    const assets = useAssets(runId, version)
    // Assets the user just asked to redraw — shown as rendering until the next assets_done (version
    // bump) clears the optimism and the refetched manifest and bytes take over.
    const [regenerating, setRegenerating] = useState<Set<string>>(new Set())

    useEffect(() => { setRegenerating(new Set()) }, [runId, version])

    const regenerate = (asset: GameAsset, prompt: string, mode: RegenMode) => {
        setRegenerating(prev => new Set(prev).add(asset.id))
        api.regenerateAsset(runId, asset.id, prompt, mode).catch(() => {
            setRegenerating(prev => {
                const next = new Set(prev)
                next.delete(asset.id)
                return next
            })
        })
    }

    const has = assets != null && assets.length > 0

    return (
        <section className="flex flex-col gap-2.5">
            <div className="flex items-center gap-3">
                <SectionLabel>Art</SectionLabel>
                {has && <span className="text-xs text-dim font-mono">{assets.length}</span>}
                {canRender && (
                    <Button onClick={onRender} disabled={acting || rendering} className="ml-auto"
                        title={has ? 'draw every asset again' : 'draw the art this game asked for'}>
                        {rendering ? 'Drawing…' : has ? 'Redraw all' : 'Draw the art'}
                    </Button>
                )}
            </div>

            {rendering && (
                <div className="text-wait text-sm flex items-center gap-2">
                    <span className="inline-block w-1.5 h-1.5 rounded-full bg-wait animate-pulse" />
                    drawing on the GPU…
                </div>
            )}

            {assets === null ? (
                <div className="text-dim text-sm">Loading…</div>
            ) : !has ? (
                <div className="text-dim text-sm">
                    {rendering ? 'Nothing drawn yet.' : 'This game asked for no art.'}
                </div>
            ) : (
                <div className="grid grid-cols-2 lg:grid-cols-3 gap-2.5">
                    {assets.map(a => (
                        <AssetCard key={a.id} runId={runId} asset={a} version={version}
                            regenerating={regenerating.has(a.id)} onRegenerate={(p, m) => regenerate(a, p, m)} />
                    ))}
                </div>
            )}
        </section>
    )
}
