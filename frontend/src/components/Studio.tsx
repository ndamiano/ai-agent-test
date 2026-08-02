import React, { useCallback, useEffect, useRef, useState } from 'react'
import { api } from '../api/client'
import { useWebSocket } from '../contexts/WebSocketContext'
import type { Game } from '../types'
import type { Route } from '../router'
import { useRouter } from '../router'
import { CreateGame } from './create/CreateGame'
import { GameView } from './game/GameView'
import { Library } from './library/Library'

const LIST_REFRESH_EVENTS = new Set([
    'prompt_proposed', 'prompt_updated', 'build_started', 'build_paused', 'build_resumed',
    'build_done', 'assets_started', 'assets_done',
])

const Studio: React.FC<{ route: Extract<Route, { kind: 'library' | 'create' | 'game' }> }> = ({ route }) => {
    const { navigate } = useRouter()
    const { messages } = useWebSocket()
    const [games, setGames] = useState<Game[]>([])
    const [loading, setLoading] = useState(false)
    const [error, setError] = useState<string | null>(null)
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

    if (route.kind === 'create') {
        return (
            <CreateGame onCancel={() => navigate('/')}
                onCreated={runId => { navigate(`/game/${runId}`, { replace: true }); refresh() }} />
        )
    }

    if (route.kind === 'game') {
        return <GameView runId={route.runId} onChanged={refresh} onBack={() => navigate('/')} />
    }

    return (
        <Library games={games} loading={loading} error={error}
            onOpen={runId => navigate(`/game/${runId}`)}
            onNew={() => navigate('/new')} />
    )
}

export default Studio
