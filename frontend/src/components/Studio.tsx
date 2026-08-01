import React, { useCallback, useEffect, useRef, useState } from 'react'
import { api } from '../api/client'
import { useWebSocket } from '../contexts/WebSocketContext'
import type { Game } from '../types'
import { CreateGame } from './create/CreateGame'
import { GameView } from './game/GameView'
import { Library } from './library/Library'

const LIST_REFRESH_EVENTS = new Set([
    'prompt_proposed', 'prompt_updated', 'build_started', 'build_paused', 'build_resumed',
    'build_done', 'assets_started', 'assets_done',
])

type View = { kind: 'library' } | { kind: 'create' } | { kind: 'game'; runId: string }

const Studio: React.FC = () => {
    const { messages } = useWebSocket()
    const [games, setGames] = useState<Game[]>([])
    const [loading, setLoading] = useState(false)
    const [error, setError] = useState<string | null>(null)
    const [view, setView] = useState<View>({ kind: 'library' })
    const seenMsgs = useRef(0)

    const refresh = useCallback(() => {
        setLoading(true)
        setError(null)
        api.listGames()
            .then(setGames)
            .catch(e => setError(e instanceof Error ? e.message : 'Failed to load games'))
            .finally(() => setLoading(false))
    }, [])

    useEffect(refresh, [refresh])

    useEffect(() => {
        if (messages.length <= seenMsgs.current) { seenMsgs.current = messages.length; return }
        const fresh = messages.slice(seenMsgs.current)
        seenMsgs.current = messages.length
        if (fresh.some(m => LIST_REFRESH_EVENTS.has(m.type))) refresh()
    }, [messages, refresh])

    const openLibrary = () => setView({ kind: 'library' })

    if (view.kind === 'create') {
        return (
            <CreateGame onCancel={openLibrary}
                onCreated={runId => { setView({ kind: 'game', runId }); refresh() }} />
        )
    }

    if (view.kind === 'game') {
        return <GameView runId={view.runId} onChanged={refresh} onBack={openLibrary} />
    }

    return (
        <Library games={games} loading={loading} error={error}
            onOpen={runId => setView({ kind: 'game', runId })}
            onNew={() => setView({ kind: 'create' })} />
    )
}

export default Studio
