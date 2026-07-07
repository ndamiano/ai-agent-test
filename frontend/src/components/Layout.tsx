import React, { useState, useEffect, useRef } from 'react'
import ChatPanel from './ChatPanel'
import GamesPanel from './GamesPanel'
import { useWebSocket } from '../contexts/WebSocketContext'
import { useAuth } from '../contexts/AuthContext'

interface LayoutProps {
    onSettingsClick: () => void
}

const Layout: React.FC<LayoutProps> = ({ onSettingsClick }) => {
    const [tab, setTab] = useState<'chat' | 'games'>('chat')
    const { messages } = useWebSocket()
    const { user, balance, logout } = useAuth()
    const [focusRun, setFocusRun] = useState<string | null>(null)
    const seenMsgs = useRef(0)

    // F2 — chat→build continuity: a chat request that drafts a spec emits `spec_proposed` (carrying
    // the new run_id) over the WebSocket. Surface it: jump to the games view and focus the run, so
    // the user lands on the freeze gate without a manual tab switch + Refresh.
    useEffect(() => {
        if (messages.length <= seenMsgs.current) { seenMsgs.current = messages.length; return }
        const fresh = messages.slice(seenMsgs.current)
        seenMsgs.current = messages.length
        const proposed = [...fresh].reverse().find(m => m.type === 'spec_proposed' && m.run_id)
        if (proposed?.run_id) { setFocusRun(proposed.run_id); setTab('games') }
    }, [messages])

    return (
        <div className="h-screen flex flex-col overflow-hidden bg-[#0f0f0f]">

            {/* Header */}
            <div className="flex-shrink-0 border-b border-white/[0.06] px-4 py-2 flex items-center justify-between bg-[#0f0f0f]">
                <div className="flex items-center gap-4">
                    <div className="text-white font-semibold text-sm tracking-wide">Maestro</div>
                    <div className="flex gap-1">
                        {(['chat', 'games'] as const).map(t => (
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
                <button
                    onClick={onSettingsClick}
                    className="text-gray-400 hover:text-white transition-colors p-1"
                    title="Settings"
                >
                    <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M10.325 4.317c.426-1.756 2.924-1.756 3.35 0a1.724 1.724 0 002.573 1.066c1.543-.94 3.31.826 2.37 2.37a1.724 1.724 0 001.065 2.572c1.756.426 1.756 2.924 0 3.35a1.724 1.724 0 00-1.066 2.573c.94 1.543-.826 3.31-2.37 2.37a1.724 1.724 0 00-2.572 1.065c-.426 1.756-2.924 1.756-3.35 0a1.724 1.724 0 00-2.573-1.066c-1.543.94-3.31-.826-2.37-2.37a1.724 1.724 0 00-1.065-2.572c-1.756-.426-1.756-2.924 0-3.35a1.724 1.724 0 001.066-2.573c-.94-1.543.826-3.31 2.37-2.37.996.608 2.296.07 2.572-1.065z" />
                        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M15 12a3 3 0 11-6 0 3 3 0 016 0z" />
                    </svg>
                </button>
                </div>
            </div>

            <div className="flex-1 overflow-hidden">
                {tab === 'chat' ? <ChatPanel /> : <GamesPanel focusRunId={focusRun} />}
            </div>
        </div>
    )
}

export default Layout
