import React, { useState } from 'react'
import { useAuth } from '../contexts/AuthContext'

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
            setError('Invalid handle or password.')
        } finally {
            setBusy(false)
        }
    }

    return (
        <div className="min-h-screen flex items-center justify-center bg-[#0f0f0f]">
            <form onSubmit={submit} className="w-72 space-y-4">
                <div className="text-white font-semibold text-lg tracking-wide text-center">Maestro</div>
                <input
                    type="text"
                    value={handle}
                    onChange={e => setHandle(e.target.value)}
                    placeholder="Handle"
                    autoFocus
                    autoComplete="username"
                    className="w-full bg-white/[0.06] text-white text-sm rounded px-3 py-2 outline-none focus:bg-white/[0.1]"
                />
                <input
                    type="password"
                    value={password}
                    onChange={e => setPassword(e.target.value)}
                    placeholder="Password"
                    autoComplete="current-password"
                    className="w-full bg-white/[0.06] text-white text-sm rounded px-3 py-2 outline-none focus:bg-white/[0.1]"
                />
                {error && <div className="text-red-400 text-xs">{error}</div>}
                <button
                    type="submit"
                    disabled={busy || !handle || !password}
                    className="w-full bg-blue-600 hover:bg-blue-700 disabled:opacity-50 text-white text-sm font-medium rounded px-3 py-2"
                >
                    {busy ? 'Signing in…' : 'Sign in'}
                </button>
                <div className="text-xs text-white/40 text-center">
                    By signing in you confirm you are 18+ and accept the{' '}
                    <a href="/terms.html" target="_blank" rel="noreferrer" className="underline hover:text-white/70">terms</a>
                    {' '}and{' '}
                    <a href="/privacy.html" target="_blank" rel="noreferrer" className="underline hover:text-white/70">privacy note</a>.
                </div>
            </form>
        </div>
    )
}

export default LoginScreen
