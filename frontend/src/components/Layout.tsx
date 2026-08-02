import React, { useEffect } from 'react'
import Studio from './Studio'
import AdminPanel from './AdminPanel'
import PromptsPanel from './PromptsPanel'
import SettingsPage from './settings/SettingsPage'
import { useAuth } from '../contexts/AuthContext'
import { Link, parseRoute, useRouter } from '../router'

const NAV: { to: string; label: string; adminOnly?: boolean }[] = [
    { to: '/', label: 'Games' },
    { to: '/admin', label: 'Admin', adminOnly: true },
    { to: '/prompts', label: 'Prompts', adminOnly: true },
]

const Layout: React.FC = () => {
    const { path, navigate } = useRouter()
    const { user, balance } = useAuth()
    const route = parseRoute(path)

    // The admin surfaces are operator-only. The server enforces it (require_admin → 403); this
    // just keeps a non-admin from landing on an empty error page via a typed URL.
    const isAdmin = user?.role === 'admin'
    const blocked = (route.kind === 'admin' || route.kind === 'prompts') && user != null && !isAdmin
    useEffect(() => { if (blocked) navigate('/', { replace: true }) }, [blocked, navigate])

    const nav = NAV.filter(n => isAdmin || !n.adminOnly)
    const navActive = (to: string) =>
        to === '/' ? ['library', 'create', 'game'].includes(route.kind) : path === to

    return (
        <div className="h-screen flex flex-col overflow-hidden bg-ink">
            <header className="flex-shrink-0 border-b border-edge bg-sunken px-4 py-2.5
                               flex items-center justify-between gap-4">
                <div className="flex items-center gap-5">
                    <Link to="/" className="font-display text-lg tracking-wide flex items-center gap-2.5
                                            hover:text-ember transition-colors">
                        <span className="w-[18px] h-[18px] rotate-45 border-[1.5px] border-ember relative
                                         after:absolute after:inset-[3px] after:bg-ember after:opacity-50" />
                        Maestro
                    </Link>
                    {nav.length > 1 && (
                        <nav className="flex gap-1">
                            {nav.map(n => (
                                <Link key={n.to} to={n.to}
                                    className={`px-3 py-1 rounded text-sm transition-colors ${
                                        navActive(n.to) ? 'bg-bone/10 text-bone' : 'text-slate hover:text-bone'
                                    }`}>
                                    {n.label}
                                </Link>
                            ))}
                        </nav>
                    )}
                </div>

                <div className="flex items-center gap-4 text-sm">
                    <span className="flex items-center gap-2 font-mono text-xs" title="credit balance">
                        <span className="w-2 h-2 rounded-full bg-mana" />
                        {balance ?? '—'} <span className="text-slate">credits</span>
                    </span>
                    {user && (
                        <Link to="/settings" title="settings"
                            className={`transition-colors ${
                                route.kind === 'settings' ? 'text-bone' : 'text-slate hover:text-bone'
                            }`}>
                            {user.handle}
                        </Link>
                    )}
                </div>
            </header>

            <main className="flex-1 overflow-hidden">
                {route.kind === 'settings' && <SettingsPage />}
                {route.kind === 'admin' && isAdmin && <AdminPanel />}
                {route.kind === 'prompts' && isAdmin && <PromptsPanel />}
                {(route.kind === 'library' || route.kind === 'create' || route.kind === 'game') && (
                    <Studio route={route} />
                )}
            </main>
        </div>
    )
}

export default Layout
