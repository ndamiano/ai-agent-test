import React from 'react'
import type { Asset } from '../../types'
import { getRenderer } from './registry'
import ThumbsControl from './ThumbsControl'
import DirtyBanner from './DirtyBanner'

// D2's central card. The shell owns the chrome (id, thumbs, dirty banner); the body is whatever
// the registry resolves for `asset.component` — SchemaCard today, a bespoke renderer once D4 lands.
const AssetCard: React.FC<{
    asset: Asset
    editable: boolean
    busy: boolean
    onSave: (content: Record<string, any>) => Promise<void>
    onThumbsUp: () => void
    onThumbsDown: (note: string) => void
}> = ({ asset, editable, busy, onSave, onThumbsUp, onThumbsDown }) => {
    // A registry lookup returns a stable function reference per component id — not actually a
    // fresh component created on every render — so the compiler's static-components rule is a
    // false positive for this exact "renderer chosen by data" pattern (the whole point of D3).
    const Renderer = getRenderer(asset.component)
    return (
        <div className="bg-[#1a1a1a] border border-white/[0.06] rounded-lg px-4 py-3 flex flex-col gap-2.5">
            <div className="flex items-center justify-between gap-2">
                <div className="text-white text-sm font-medium font-mono">{asset.id}</div>
                <ThumbsControl dirty={asset.dirty} busy={busy} onThumbsUp={onThumbsUp} onThumbsDown={onThumbsDown} />
            </div>
            {asset.dirty && <DirtyBanner note={asset.review_note} />}
            {/* eslint-disable-next-line react-hooks/static-components */}
            <Renderer asset={asset} editable={editable} onSave={onSave} />
        </div>
    )
}

export default AssetCard
