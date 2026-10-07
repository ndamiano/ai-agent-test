import React, { useState } from 'react'
import { api } from '../../api/client'
import { Button } from '../ui/Button'

export const ShareLink: React.FC<{ runId: string; initial: string | null }> = ({ runId, initial }) => {
    const [shareId, setShareId] = useState<string | null>(initial)
    const [busy, setBusy] = useState(false)
    const [copied, setCopied] = useState(false)
    const [error, setError] = useState<string | null>(null)
    const url = shareId ? `${window.location.origin}/g/${shareId}` : ''

    const act = async (fn: () => Promise<string | null>) => {
        setBusy(true); setError(null); setCopied(false)
        try { setShareId(await fn()) }
        catch { setError('That didn’t work — try again in a moment.') }
        finally { setBusy(false) }
    }
    const share = () => act(async () => (await api.shareGame(runId)).share_id)
    const unshare = () => act(async () => (await api.unshareGame(runId)).share_id)
    const copy = async () => {
        try { await navigator.clipboard.writeText(url); setCopied(true) }
        catch { setCopied(false) }
    }

    return (
        <section className="flex flex-col gap-2">
            {shareId ? (
                <>
                    <div className="flex gap-2">
                        <input readOnly value={url} onFocus={e => e.currentTarget.select()}
                            aria-label="share link"
                            className="flex-1 min-w-0 rounded border border-edge bg-sunken px-2.5 py-1.5
                                       text-xs text-bone" />
                        <Button variant="primary" onClick={copy}>{copied ? 'Copied' : 'Copy'}</Button>
                    </div>
                    <p className="text-xs text-dim">
                        Anyone with this link can play the copy you shared. Changes you make stay
                        private until you update it.
                    </p>
                    <div className="flex gap-2">
                        <Button onClick={share} disabled={busy}>
                            {busy ? 'Working…' : 'Update shared copy'}
                        </Button>
                        <Button variant="ghost" onClick={unshare} disabled={busy}>Stop sharing</Button>
                    </div>
                </>
            ) : (
                <>
                    <Button variant="ghost" size="md" onClick={share} disabled={busy}>
                        {busy ? 'Making a link…' : 'Share a link'}
                    </Button>
                    <p className="text-xs text-dim">Your game stays private unless you share it.</p>
                </>
            )}
            {error && <p className="text-xs text-fail">{error}</p>}
        </section>
    )
}
