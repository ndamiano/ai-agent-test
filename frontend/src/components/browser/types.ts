import type React from 'react'
import type { Asset } from '../../types'

// The body of a card, chosen from the registry by `asset.component`. The shell (Tabs, NavStrip,
// thumbs, dirty banner) is component-blind; only the renderer knows how to present its content.
export type AssetRenderer = React.FC<{
    asset: Asset
    editable: boolean
    onSave: (content: Record<string, unknown>) => Promise<void>
}>
