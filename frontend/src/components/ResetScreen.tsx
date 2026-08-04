import React, { useState } from 'react'
import { ApiError } from '../api/client'
import { useAuth } from '../contexts/AuthContext'
import { Link, useRouter } from '../router'
import { Button } from './ui/Button'
import { TextInput } from './ui/Field'

// The end of the emailed reset link. `?t=` is the whole credential, so this view is reached without
// a session and succeeding on it signs the user in.
const ResetScreen: React.FC = () => {
    const { resetPassword } = useAuth()
    const { navigate } = useRouter()
    const [resetToken] = useState(() => new URLSearchParams(window.location.search).get('t') ?? '')
    const [next, setNext] = useState('')
    const [confirm, setConfirm] = useState('')
    const [error, setError] = useState<string | null>(null)
    const [busy, setBusy] = useState(false)

    const submit = async (e: React.FormEvent) => {
        e.preventDefault()
        if (next !== confirm) {
            setError('The new passwords do not match.')
            return
        }
        setBusy(true)
        setError(null)
        try {
            await resetPassword(resetToken, next)
            navigate('/')
        } catch (err) {
            setError(err instanceof ApiError && typeof err.body === 'string' && err.body
                ? err.body.charAt(0).toUpperCase() + err.body.slice(1) + '.'
                : 'Could not set the new password.')
        } finally {
            setBusy(false)
        }
    }

    return (
        <div className="min-h-screen flex items-center justify-center bg-ink px-6">
            <div className="w-80 flex flex-col gap-4">
                <div className="font-display text-2xl tracking-wide text-center flex items-center justify-center gap-2.5">
                    <span className="w-[18px] h-[18px] rotate-45 border-[1.5px] border-ember relative
                                     after:absolute after:inset-[3px] after:bg-ember after:opacity-50" />
                    Maestro
                </div>

                {resetToken ? (
                    <form onSubmit={submit} className="flex flex-col gap-4">
                        <p className="text-sm text-slate text-center leading-relaxed">
                            Choose a new password.
                        </p>
                        <TextInput type="password" value={next} onChange={e => setNext(e.target.value)}
                            placeholder="New password" autoFocus autoComplete="new-password" disabled={busy} />
                        <TextInput type="password" value={confirm} onChange={e => setConfirm(e.target.value)}
                            placeholder="New password, again" autoComplete="new-password" disabled={busy} />
                        <p className="text-xs text-dim -mt-2">At least 10 characters.</p>

                        {error && <div className="text-fail text-sm">{error}</div>}

                        <Button type="submit" variant="primary" size="md" disabled={busy || !next || !confirm}>
                            {busy ? 'Setting…' : 'Set password'}
                        </Button>
                    </form>
                ) : (
                    <p className="text-sm text-fail text-center leading-relaxed">
                        That reset link is missing its token. Ask for a new one from the sign-in page.
                    </p>
                )}

                <Link to="/login" className="text-xs text-dim text-center hover:text-slate transition-colors">
                    Back to sign in
                </Link>
            </div>
        </div>
    )
}

export default ResetScreen
