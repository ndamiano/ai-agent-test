import React from 'react'

// D7 — the card banner showing why an asset is flagged.
const DirtyBanner: React.FC<{ note: string }> = ({ note }) => (
    <div className="bg-amber-500/10 border border-amber-500/30 rounded px-3 py-1.5 text-amber-300 text-xs">
        {note ? <>Flagged for review: {note}</> : 'Flagged for review.'}
    </div>
)

export default DirtyBanner
