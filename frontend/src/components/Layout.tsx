import React, { useState, useEffect, useRef } from 'react'
import ChatPanel from './ChatPanel'
import GamesPanel from './GamesPanel'
import { useWebSocket } from '../contexts/WebSocketContext'
import { useAuth } from '../contexts/AuthContext'

const Layout: React.FC = () => {
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
                </div>
            </div>

            <div className="flex-1 overflow-hidden">
                {tab === 'chat' ? <ChatPanel /> : <GamesPanel focusRunId={focusRun} />}
            </div>
        </div>
    )
}

export default Layout
