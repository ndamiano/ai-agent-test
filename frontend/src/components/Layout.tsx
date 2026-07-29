import React, { useState } from 'react'
import GamesPanel from './GamesPanel'
import AdminPanel from './AdminPanel'
import PromptsPanel from './PromptsPanel'
import { useAuth } from '../contexts/AuthContext'

type Tab = 'games' | 'admin' | 'prompts'

const Layout: React.FC = () => {
    const [tab, setTab] = useState<Tab>('games')
    const { user, balance, logout } = useAuth()

    // The admin tab is operator-only. The server enforces it (require_admin → 403); this just hides
    // the entry point from ordinary users so it never shows.
    const tabs: Tab[] = user?.role === 'admin' ? ['games', 'admin', 'prompts'] : ['games']

    return (
        <div className="h-screen flex flex-col overflow-hidden bg-[#0f0f0f]">

            <div className="flex-shrink-0 border-b border-white/[0.06] px-4 py-2 flex items-center justify-between bg-[#0f0f0f]">
                <div className="flex items-center gap-4">
                    <div className="text-white font-semibold text-sm tracking-wide">Maestro</div>
                    <div className="flex gap-1">
                        {tabs.map(t => (
                            <button
                                key={t}
                                onClick={() => setTab(t)}
                                className={`px-3 py-1 rounded text-xs font-medium transition-colors capitalize ${
                                    tab === t
                                        ? 'bg-white/10 text-white'
                                        : 'text-gray-500 hover:text-gray-300'
                                }`}
                            >
                                {t}
                            </button>
                        ))}
                    </div>
                </div>
                <div className="flex items-center gap-3">
                    <span className="text-xs text-gray-300" title="credit balance">
                        {balance ?? '—'} <span className="text-gray-500">credits</span>
                    </span>
                    {user && (
                        <button
                            onClick={logout}
                            className="text-gray-500 hover:text-gray-300 transition-colors text-xs"
                            title={`Sign out ${user.handle}`}
                        >
                            Sign out
                        </button>
                    )}
                </div>
            </div>

            <div className="flex-1 overflow-hidden">
                {tab === 'games' && <GamesPanel />}
                {tab === 'admin' && <AdminPanel />}
                {tab === 'prompts' && <PromptsPanel />}
            </div>
        </div>
    )
}

export default Layout
