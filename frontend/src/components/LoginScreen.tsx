import React, { useState } from 'react'
import { useAuth } from '../contexts/AuthContext'
import { Button } from './ui/Button'
import { TextInput } from './ui/Field'

// The login gate. Accounts are provisioned by an admin (`python -m auth.cli create`) — there is
// deliberately no signup here.
const LoginScreen: React.FC = () => {
    const { login } = useAuth()
    const [handle, setHandle] = useState('')
    const [password, setPassword] = useState('')
    const [error, setError] = useState<string | null>(null)
    const [busy, setBusy] = useState(false)

    const submit = async (e: React.FormEvent) => {
        e.preventDefault()
        setBusy(true)
        setError(null)
        try {
            await login(handle, password)
        } catch {
            setError('That handle and password do not match an account.')
        } finally {
            setBusy(false)
        }
    }

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
                    placeholder="Password" autoComplete="current-password" />

                {error && <div className="text-fail text-sm">{error}</div>}

                <Button type="submit" variant="primary" size="md" disabled={busy || !handle || !password}>
                    {busy ? 'Signing in…' : 'Sign in'}
                </Button>

                <p className="text-xs text-dim text-center leading-relaxed">
                    By signing in you confirm you are 18+ and accept the{' '}
                    <a href="/terms.html" target="_blank" rel="noreferrer" className="underline hover:text-slate">terms</a>
                    {' '}and{' '}
                    <a href="/privacy.html" target="_blank" rel="noreferrer" className="underline hover:text-slate">privacy note</a>.
                </p>
            </form>
        </div>
    )
}

export default LoginScreen
