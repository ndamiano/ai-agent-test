import React, { useState } from 'react'
import { api } from '../../api/client'
import { useAuth } from '../../contexts/AuthContext'
import { Link } from '../../router'
import { Button } from '../ui/Button'
import { SectionLabel, TextInput } from '../ui/Field'

const Row: React.FC<{ label: string; children: React.ReactNode }> = ({ label, children }) => (
    <div className="flex items-baseline justify-between gap-4 text-sm">
        <span className="text-slate">{label}</span>
        <span className="text-bone">{children}</span>
    </div>
)

const SettingsPage: React.FC = () => {
    const { user, balance, logout } = useAuth()
    const [current, setCurrent] = useState('')
    const [next, setNext] = useState('')
    const [confirm, setConfirm] = useState('')
    const [busy, setBusy] = useState(false)
    const [message, setMessage] = useState<{ text: string; ok: boolean } | null>(null)

    const canSubmit = current && next && confirm && !busy

    const changePassword = async () => {
        if (next !== confirm) {
            setMessage({ text: 'The new passwords do not match.', ok: false })
            return
        }
        setBusy(true); setMessage(null)
        try {
            await api.changePassword(current, next)
            setCurrent(''); setNext(''); setConfirm('')
            setMessage({ text: 'Password changed.', ok: true })
        } catch (e) {
            setMessage({ text: e instanceof Error ? e.message : 'Could not change the password', ok: false })
        } finally { setBusy(false) }
    }

    return (
        <div className="h-full overflow-y-auto">
            <div className="max-w-xl mx-auto px-6 py-8 flex flex-col gap-8">
                <h2 className="font-display text-2xl">Settings</h2>

                <section className="flex flex-col gap-3">
                    <SectionLabel>Account</SectionLabel>
                    <div className="border border-edge rounded-md bg-sunken px-4 py-3 flex flex-col gap-2">
                        <Row label="Handle">{user?.handle ?? '—'}</Row>
                        <Row label="Role">{user?.role ?? '—'}</Row>
                        <Row label="Credits">
                            <span className="flex items-baseline gap-3">
                                <span>{balance ?? '—'}</span>
                                <Link to="/credits" className="text-ember text-xs hover:underline">
                                    Buy credits
                                </Link>
                            </span>
                        </Row>
                    </div>
                </section>

                <section className="flex flex-col gap-3">
                    <SectionLabel>Change password</SectionLabel>
                    <form className="flex flex-col gap-2.5"
                        onSubmit={e => { e.preventDefault(); if (canSubmit) changePassword() }}>
                        <TextInput type="password" autoComplete="current-password" placeholder="Current password"
                            value={current} onChange={e => setCurrent(e.target.value)} disabled={busy} />
                        <TextInput type="password" autoComplete="new-password" placeholder="New password"
                            value={next} onChange={e => setNext(e.target.value)} disabled={busy} />
                        <TextInput type="password" autoComplete="new-password" placeholder="New password, again"
                            value={confirm} onChange={e => setConfirm(e.target.value)} disabled={busy} />
                        <div className="flex items-center gap-3">
                            <Button type="submit" variant="primary" size="md" disabled={!canSubmit}>
                                {busy ? 'Changing…' : 'Change password'}
                            </Button>
                            {message && (
                                <span className={`text-sm ${message.ok ? 'text-live' : 'text-fail'}`}>
                                    {message.text}
                                </span>
                            )}
                        </div>
                    </form>
                </section>

                <section className="flex flex-col gap-3">
                    <SectionLabel>Session</SectionLabel>
                    <div>
                        <Button variant="danger" size="md" onClick={logout}>Sign out</Button>
                    </div>
                </section>
            </div>
        </div>
    )
}

export default SettingsPage
