import React, { useState } from 'react'
import Studio from './Studio'
import AdminPanel from './AdminPanel'
import PromptsPanel from './PromptsPanel'
import { useAuth } from '../contexts/AuthContext'

type Tab = 'games' | 'admin' | 'prompts'

const TAB_LABEL: Record<Tab, string> = { games: 'Games', admin: 'Admin', prompts: 'Prompts' }

const Layout: React.FC = () => {
    const [tab, setTab] = useState<Tab>('games')
    const { user, balance, logout } = useAuth()

    // The admin tabs are operator-only. The server enforces it (require_admin → 403); this just
    // hides the entry point from ordinary users so it never shows.
    const tabs: Tab[] = user?.role === 'admin' ? ['games', 'admin', 'prompts'] : ['games']

    return (
        <div className="h-screen flex flex-col overflow-hidden bg-ink">
            <header className="flex-shrink-0 border-b border-edge bg-sunken px-4 py-2.5
                               flex items-center justify-between gap-4">
                <div className="flex items-center gap-5">
                    <div className="font-display text-lg tracking-wide flex items-center gap-2.5">
                        <span className="w-[18px] h-[18px] rotate-45 border-[1.5px] border-ember relative
                                         after:absolute after:inset-[3px] after:bg-ember after:opacity-50" />
                        Maestro
                    </div>
                    {tabs.length > 1 && (
                        <nav className="flex gap-1">
                            {tabs.map(t => (
                                <button key={t} onClick={() => setTab(t)}
                                    className={`px-3 py-1 rounded text-sm transition-colors ${
                                        tab === t ? 'bg-bone/10 text-bone' : 'text-slate hover:text-bone'
                                    }`}>
                                    {TAB_LABEL[t]}
                                </button>
                            ))}
                        </nav>
                    )}
                </div>

                <div className="flex items-center gap-4 text-sm">
                    <span className="flex items-center gap-2 font-mono text-xs" title="credit balance">
                        <span className="w-2 h-2 rounded-full bg-mana" />
                        {balance ?? '—'} <span className="text-slate">credits</span>
                    </span>
                    {user && <span className="text-bone">{user.handle}</span>}
                    {user && (
                        <button onClick={logout}
                            className="text-slate hover:text-bone transition-colors text-sm">
                            Sign out
                        </button>
                    )}
                </div>
            </header>

            <main className="flex-1 overflow-hidden">
                {tab === 'games' && <Studio />}
                {tab === 'admin' && <AdminPanel />}
                {tab === 'prompts' && <PromptsPanel />}
            </main>
        </div>
    )
}

export default Layout
