import React from 'react'
import type { GameAsset, GameDetail } from '../../types'
import { LinkButton } from '../ui/Button'
import { Meter } from '../ui/Meter'
import { Sigil } from '../ui/Sigil'
import { AssetGallery } from './AssetGallery'
import { FixNote } from './FixNote'
import { PromptBox } from './PromptBox'
import { useAssetBlob } from './useAssets'

// The staged game itself is the largest thing on the screen, with its own first image behind the
// Play control — or its sigil, when the build rendered no art.
const Stage: React.FC<{ runId: string; cover: GameAsset | null; version: number; playUrl: string | null }> = ({
    runId, cover, version, playUrl,
}) => {
    const url = useAssetBlob(runId, cover, version)

    return (
        <div className="relative aspect-video rounded-md border border-edge bg-sunken overflow-hidden grid place-items-center">
            {url
                ? <img src={url} alt="" className="absolute inset-0 w-full h-full object-cover opacity-70" />
                : <div className="absolute inset-0"><Sigil seed={runId} /></div>}
            {playUrl && (
                <LinkButton href={playUrl} target="_blank" rel="noreferrer" size="lg"
                    className="relative shadow-[0_10px_40px_-12px_rgb(var(--c-ember)/0.9)]">
                    ▶ Play
                </LinkButton>
            )}
        </div>
    )
}

export const BuiltPanel: React.FC<{
    runId: string
    detail: GameDetail
    assets: GameAsset[] | null
    assetsVersion: number
    budget: number | null
    rendering: boolean
    acting: boolean
    onRender: () => void
    note: string
    setNote: (v: string) => void
    onFix: () => void
    promptText: string
    setPromptText: (v: string) => void
}> = ({
    runId, detail, assets, assetsVersion, budget, rendering, acting, onRender,
    note, setNote, onFix, promptText, setPromptText,
}) => {
    const cover = assets?.find(a => a.kind !== 'mesh' && a.status === 'ready') ?? null

    return (
        <div className="grid gap-6 lg:grid-cols-[minmax(0,1.4fr)_minmax(0,1fr)]">
            <div className="flex flex-col gap-6 min-w-0">
                <Stage runId={runId} cover={cover} version={assetsVersion} playUrl={detail.play_url} />
                <FixNote note={note} setNote={setNote} onSubmit={onFix} busy={acting} />
                <PromptBox prompt={detail.prompt} disabled={false} text={promptText} onChange={setPromptText} />
            </div>

            <div className="flex flex-col gap-6 min-w-0">
                {budget != null && (
                    <section className="flex flex-col gap-2">
                        <Meter value={budget} label="Compute left" />
                    </section>
                )}
                <AssetGallery runId={runId} version={assetsVersion} rendering={rendering}
                    canRender onRender={onRender} acting={acting} />
            </div>
        </div>
    )
}
