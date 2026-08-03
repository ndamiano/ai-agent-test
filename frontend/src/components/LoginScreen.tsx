import React, { useState } from 'react'
import { ApiError } from '../api/client'
import { useAuth } from '../contexts/AuthContext'
import { Link } from '../router'
import { Button } from './ui/Button'
import { TextInput } from './ui/Field'

// The login gate, and beta signup behind it: an account is created against an admin-minted
// invite code (`/api/admin/invites`) — there is no open signup.
const signupError = (e: unknown): string => {
    if (e instanceof ApiError) {
        if (e.status === 409) return 'That handle is already taken.'
        // 403 carries the code verdict: invalid, disabled, or used up.
        if (typeof e.body === 'string' && e.body) return e.body.charAt(0).toUpperCase() + e.body.slice(1) + '.'
    }
    return 'Could not create the account.'
}

const LoginScreen: React.FC = () => {
    const { login, signup } = useAuth()
    const [mode, setMode] = useState<'login' | 'signup'>('login')
    const [handle, setHandle] = useState('')
    const [password, setPassword] = useState('')
    const [inviteCode, setInviteCode] = useState('')
    const [error, setError] = useState<string | null>(null)
    const [busy, setBusy] = useState(false)

    const submit = async (e: React.FormEvent) => {
        e.preventDefault()
        setBusy(true)
        setError(null)
        try {
            if (mode === 'login') await login(handle, password)
            else await signup(handle, password, inviteCode)
        } catch (err) {
            if (err instanceof ApiError && err.status === 429) {
                setError('Too many attempts — wait a few minutes and try again.')
            } else {
                setError(mode === 'login'
                    ? 'That handle and password do not match an account.'
                    : signupError(err))
            }
        } finally {
            setBusy(false)
        }
    }

    const switchMode = (to: 'login' | 'signup') => {
        setMode(to)
        setError(null)
    }

    const ready = mode === 'login' ? handle && password : handle && password && inviteCode

    return (
        <div className="min-h-screen flex items-center justify-center bg-ink px-6">
            <form onSubmit={submit} className="w-80 flex flex-col gap-4">
                <div className="font-display text-2xl tracking-wide text-center flex items-center justify-center gap-2.5">
                    <span className="w-[18px] h-[18px] rotate-45 border-[1.5px] border-ember relative
                                     after:absolute after:inset-[3px] after:bg-ember after:opacity-50" />
                    Maestro
                </div>

                <TextInput type="text" value={handle} onChange={e => setHandle(e.target.value)}
                    placeholder="Handle" autoFocus autoComplete="username" />
                <TextInput type="password" value={password} onChange={e => setPassword(e.target.value)}
                    placeholder="Password"
                    autoComplete={mode === 'login' ? 'current-password' : 'new-password'} />
                {mode === 'signup' && (
                    <TextInput type="text" value={inviteCode} onChange={e => setInviteCode(e.target.value)}
                        placeholder="Invite code (gs-xxxx-xxxx)" autoComplete="off" />
                )}

                {error && <div className="text-fail text-sm">{error}</div>}

                <Button type="submit" variant="primary" size="md" disabled={busy || !ready}>
                    {mode === 'login'
                        ? (busy ? 'Signing in…' : 'Sign in')
                        : (busy ? 'Creating account…' : 'Create account')}
                </Button>

                {mode === 'login' ? (
                    <button type="button" onClick={() => switchMode('signup')}
                        className="text-xs text-dim text-center hover:text-slate transition-colors">
                        Have an invite code? Create an account
                    </button>
                ) : (
                    <button type="button" onClick={() => switchMode('login')}
                        className="text-xs text-dim text-center hover:text-slate transition-colors">
                        Already have an account? Sign in
                    </button>
                )}

                <Link to="/" className="text-xs text-dim text-center hover:text-slate transition-colors">
                    ← Back to the demos
                </Link>

                <p className="text-xs text-dim text-center leading-relaxed">
                    By {mode === 'login' ? 'signing in' : 'creating an account'} you confirm you are 18+ and accept the{' '}
                    <a href="/terms.html" target="_blank" rel="noreferrer" className="underline hover:text-slate">terms</a>
                    {' '}and{' '}
                    <a href="/privacy.html" target="_blank" rel="noreferrer" className="underline hover:text-slate">privacy note</a>.
                </p>
            </form>
        </div>
    )
}

export default LoginScreen
