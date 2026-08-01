import { useEffect, useState } from 'react'
import { api } from '../../api/client'
import type { GameAsset } from '../../types'

// The manifest of what a game asked for. `version` bumps on assets_done so a finished render or a
// regenerated file is picked up.
export const useAssets = (runId: string, version: number): GameAsset[] | null => {
    const [assets, setAssets] = useState<GameAsset[] | null>(null)

    useEffect(() => {
        let cancelled = false
        setAssets(null)
        api.getGameAssets(runId)
            .then(a => { if (!cancelled) setAssets(a) })
            .catch(() => { if (!cancelled) setAssets([]) })
        return () => { cancelled = true }
    }, [runId, version])

    return assets
}

// One asset's bytes as an object URL. The blob route is authed, so <img src> can't reach it
// directly — fetch it and revoke the URL when the asset changes or the component unmounts.
// `version` is in the deps because a regenerated file swaps under an unchanged id and status.
export const useAssetBlob = (runId: string, asset: GameAsset | null, version: number): string | null => {
    const [url, setUrl] = useState<string | null>(null)
    const previewable = !!asset && asset.kind !== 'mesh' && asset.status === 'ready'
    const assetId = asset?.id

    useEffect(() => {
        if (!previewable || !assetId) { setUrl(null); return }
        let live = true
        let made: string | null = null
        api.getAssetBlobUrl(runId, assetId)
            .then(u => { if (live) { made = u; setUrl(u) } else URL.revokeObjectURL(u) })
            .catch(() => { if (live) setUrl(null) })
        return () => { live = false; if (made) URL.revokeObjectURL(made) }
    }, [runId, assetId, previewable, version])

    return url
}
