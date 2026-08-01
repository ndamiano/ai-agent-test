import React from 'react'
import type { GameAsset } from '../../types'
import { useAssetBlob } from './useAssets'

const Thumb: React.FC<{ runId: string; asset: GameAsset; version: number }> = ({
    runId, asset, version,
}) => {
    const url = useAssetBlob(runId, asset, version)

    if (asset.status !== 'ready') {
        return (
            <span title={asset.id}
                className="w-12 h-12 rounded border border-edge bg-sunken grid place-items-center
                           text-[10px] font-mono text-dim">
                {asset.status === 'rendering' ? 'drawing' : 'queued'}
            </span>
        )
    }
    return (
        <span title={asset.id}
            className="w-12 h-12 rounded border border-edge bg-sunken grid place-items-center overflow-hidden">
            {url
                ? <img src={url} alt={asset.id} className="max-w-full max-h-full object-contain [image-rendering:pixelated]" />
                : <span className="text-base opacity-40">⬡</span>}
        </span>
    )
}

// What the game asked for while it is still writing the code that uses it.
export const AssetStrip: React.FC<{ runId: string; assets: GameAsset[]; version: number; max?: number }> = ({
    runId, assets, version, max = 8,
}) => (
    <div className="flex gap-2 flex-wrap">
        {assets.slice(0, max).map(a => <Thumb key={a.id} runId={runId} asset={a} version={version} />)}
        {assets.length > max && (
            <span className="w-12 h-12 rounded border border-edge grid place-items-center text-xs font-mono text-dim">
                +{assets.length - max}
            </span>
        )}
    </div>
)
