import React, { useEffect, useState } from 'react'
import type { AssetRenderer } from '../types'
import { api } from '../../../api/client'
import { Pill, RawFieldsFallback } from './shared'
import type {
    AssetManifestAsset, AssetManifestEntry, AssetManifestTitleCard, WorldFeature,
} from '../../../types/assets'

// Byte-for-byte port of renpy/fns.py's tile_slug: lowercase, [a-z0-9] kept, every other run
// collapses to one '_', ends trimmed — so a feature's label maps to the exact filename generation
// wrote (feature_<slug>.png / .glb).
function slug(theme: string): string {
    let out = ''
    let prevUnderscore = false
    for (const ch of theme.toLowerCase()) {
        if (/[a-z0-9]/.test(ch)) { out += ch; prevUnderscore = false }
        else if (!prevUnderscore) { out += '_'; prevUnderscore = true }
    }
    return out.replace(/^_+|_+$/g, '')
}

const STATUS_DOT: Record<'loading' | 'ok' | 'missing', string> = {
    loading: 'bg-gray-500 animate-pulse',
    ok: 'bg-emerald-400',
    missing: 'bg-red-400',
}

// One gallery tile: thumbnail (the served file, cache-busted after a regen), status dot, and a
// Regenerate button that hits the per-asset endpoint and refreshes just this thumbnail — never
// the whole manifest.
const AssetThumb: React.FC<{
    runId: string
    filename: string
    label: string
    description?: string
    editable: boolean
    extra?: React.ReactNode
    onRegenerated?: () => void
}> = ({ runId, filename, label, description, editable, extra, onRegenerated }) => {
    const [version, setVersion] = useState(0)
    const [status, setStatus] = useState<'loading' | 'ok' | 'missing'>('loading')
    const [busy, setBusy] = useState(false)
    const [err, setErr] = useState<string | null>(null)

    const url = `${api.assetFileUrl(runId, filename)}${version ? `?v=${version}` : ''}`

    const regenerate = async () => {
        setBusy(true); setErr(null)
        try {
            await api.regenerateAsset(runId, filename)
            setStatus('loading')
            setVersion(v => v + 1)
            onRegenerated?.()
        } catch (e) {
            setErr(e instanceof Error ? e.message : 'regeneration failed')
        } finally {
            setBusy(false)
        }
    }

    return (
        <div className="bg-black/20 rounded-lg p-2 flex flex-col gap-1.5 w-32">
            <div className="relative w-28 h-28 rounded overflow-hidden bg-white/[0.04]">
                <img
                    key={url}
                    src={url}
                    alt={label}
                    className="w-full h-full object-cover"
                    onLoad={() => setStatus('ok')}
                    onError={() => setStatus('missing')}
                />
                <span
                    className={`absolute top-1 right-1 w-2 h-2 rounded-full ${STATUS_DOT[status]}`}
                    title={status === 'ok' ? 'generated' : status === 'missing' ? 'missing' : 'loading'}
                />
            </div>
            <div className="text-[11px] text-gray-300 font-mono truncate" title={filename}>{label}</div>
            {description && (
                <div className="text-[10px] text-gray-600 line-clamp-2" title={description}>{description}</div>
            )}
            {extra}
            {editable && (
                <button
                    onClick={regenerate}
                    disabled={busy}
                    className="mt-0.5 text-[10px] px-1.5 py-1 rounded bg-white/[0.06] hover:bg-white/[0.1] text-gray-300 disabled:opacity-50 disabled:cursor-not-allowed"
                >
                    {busy ? 'Regenerating…' : 'Regenerate'}
                </button>
            )}
            {err && <div className="text-[10px] text-red-400">{err}</div>}
        </div>
    )
}

// A feature sprite's mesh half — a HEAD probe against the served .glb (no in-browser 3D viewer;
// the source PNG thumbnail plus this ✓/✗ is the honest summary).
const MeshBadge: React.FC<{ runId: string; featureSlug: string; version: number }> = ({ runId, featureSlug, version }) => {
    const [has, setHas] = useState<boolean | null>(null)

    useEffect(() => {
        let cancelled = false
        setHas(null)
        fetch(`${api.assetFileUrl(runId, `feature_${featureSlug}.glb`)}${version ? `?v=${version}` : ''}`,
            { method: 'HEAD' })
            .then(res => { if (!cancelled) setHas(res.ok) })
            .catch(() => { if (!cancelled) setHas(false) })
        return () => { cancelled = true }
    }, [runId, featureSlug, version])

    return (
        <Pill tone={has === true ? 'green' : has === false ? 'red' : 'gray'}>
            {has === null ? '3D mesh …' : has ? '3D mesh ✓' : '3D mesh ✗'}
        </Pill>
    )
}

const Section: React.FC<{ title: string; count: number; children: React.ReactNode }> = ({ title, count, children }) => {
    if (count === 0) return null
    return (
        <div className="mt-3 first:mt-0">
            <div className="text-gray-500 text-[10px] uppercase tracking-wide mb-1.5">{title} ({count})</div>
            <div className="flex flex-wrap gap-2">{children}</div>
        </div>
    )
}

// A feature's mesh regen rides its sprite's Regenerate button (the backend chains the .glb when
// presentation is hd2d) — bumping the mesh badge's cache-bust key on that same callback re-probes
// its ✓/✗ afterward, so one thumb + one button covers both halves of the asset.
const FeatureThumb: React.FC<{ runId: string; feature: WorldFeature; editable: boolean }> = ({ runId, feature, editable }) => {
    const s = slug(feature.label)
    const [meshVersion, setMeshVersion] = useState(0)
    return (
        <AssetThumb
            runId={runId} filename={`feature_${s}.png`} label={feature.label} description={feature.kind}
            editable={editable} onRegenerated={() => setMeshVersion(v => v + 1)}
            extra={<MeshBadge runId={runId} featureSlug={s} version={meshVersion} />}
        />
    )
}

// The declared-asset gallery: every backgrounds/characters/items/cgs/title_card entry from the
// manifest, rendered against its ACTUAL file on disk (via the run's asset-file route), each with
// its own Regenerate control (no all-or-nothing "regenerate images" here). World features (hd2d
// walkable maps) are folded in from the places component so a feature's mesh coverage is visible
// alongside its source sprite.
const AssetManifestCard: AssetRenderer = ({ asset, editable, onSave }) => {
    const a = asset as AssetManifestAsset
    const c = a.content ?? {}
    const runId = a.run_id
    const backgrounds: AssetManifestEntry[] = Array.isArray(c.backgrounds) ? c.backgrounds : []
    const characters: AssetManifestEntry[] = Array.isArray(c.characters) ? c.characters : []
    const items: AssetManifestEntry[] = Array.isArray(c.items) ? c.items : []
    const cgs: AssetManifestEntry[] = Array.isArray(c.cgs) ? c.cgs : []
    const titleCard: AssetManifestTitleCard | undefined = c.title_card

    const [features, setFeatures] = useState<WorldFeature[]>([])

    useEffect(() => {
        if (!runId) return
        let cancelled = false
        api.listAssets(runId, 'places').then(rows => {
            if (cancelled) return
            const seen = new Map<string, WorldFeature>()
            for (const row of rows) {
                const feats = row.content?.layout?.features
                if (!Array.isArray(feats)) continue
                for (const f of feats) {
                    if (f?.label && !seen.has(f.label)) {
                        seen.set(f.label, { id: f.id ?? f.label, kind: f.kind, label: f.label })
                    }
                }
            }
            setFeatures(Array.from(seen.values()))
        }).catch(() => { /* no places component (VN/PnC games have none) */ })
        return () => { cancelled = true }
    }, [runId])

    if (!runId) {
        return <p className="text-red-400 text-xs">This asset has no run id — cannot load its images.</p>
    }

    return (
        <div>
            <Section title="Backgrounds" count={backgrounds.length}>
                {backgrounds.map(bg => (
                    <AssetThumb key={bg.id} runId={runId} editable={editable}
                        filename={bg.image_file || `${bg.id}.png`} label={bg.id} description={bg.description} />
                ))}
            </Section>

            <Section title="Characters" count={characters.length}>
                {characters.map(ch => (
                    <AssetThumb key={ch.id} runId={runId} editable={editable}
                        filename={ch.image_file || `${ch.id}.png`} label={ch.id} description={ch.description} />
                ))}
            </Section>

            <Section title="Items" count={items.length}>
                {items.map(it => (
                    <AssetThumb key={it.id} runId={runId} editable={editable}
                        filename={it.image_file || `${it.id}.png`} label={it.id} description={it.description} />
                ))}
            </Section>

            <Section title="CGs" count={cgs.length}>
                {cgs.map(cg => (
                    <AssetThumb key={cg.id} runId={runId} editable={editable}
                        filename={cg.image_file || `${cg.id}.png`} label={cg.id} description={cg.description} />
                ))}
            </Section>

            <Section title="Title Card" count={titleCard?.image_file ? 1 : 0}>
                {titleCard?.image_file && (
                    <AssetThumb runId={runId} editable={editable}
                        filename={titleCard.image_file} label="title_card" description={titleCard.description} />
                )}
            </Section>

            <Section title="World Features" count={features.length}>
                {features.map(f => (
                    <FeatureThumb key={f.id} runId={runId} feature={f} editable={editable} />
                ))}
            </Section>

            <RawFieldsFallback asset={asset} editable={editable} onSave={onSave} />
        </div>
    )
}

export default AssetManifestCard
