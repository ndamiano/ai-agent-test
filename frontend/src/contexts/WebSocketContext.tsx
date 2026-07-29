import React, { createContext, useContext, useEffect, useState, useRef, useCallback } from 'react'
import type { WebSocketMessage } from '../types'
import { useAuth } from './AuthContext'

interface WebSocketContextValue {
    messages: WebSocketMessage[]
    connected: boolean
    subscribe: (runId: string, callback: (message: WebSocketMessage) => void) => () => void
}

const WebSocketContext = createContext<WebSocketContextValue | null>(null)

const BASE_DELAY_MS = 1000
const MAX_DELAY_MS = 30000
const MAX_RETRIES = 10

function getBackoffDelay(attempt: number): number {
    return Math.min(BASE_DELAY_MS * 2 ** attempt, MAX_DELAY_MS)
}

export const WebSocketProvider: React.FC<{ children: React.ReactNode }> = ({ children }) => {
    const { token } = useAuth()
    const [messages, setMessages] = useState<WebSocketMessage[]>([])
    const [connected, setConnected] = useState(false)
    const wsRef = useRef<WebSocket | null>(null)
    const retryCountRef = useRef(0)
    const reconnectTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null)
    const unmountedRef = useRef(false)
    const subscribersRef = useRef<Map<string, Set<(message: WebSocketMessage) => void>>>(new Map())

    const subscribe = useCallback((runId: string, callback: (message: WebSocketMessage) => void) => {
        if (!subscribersRef.current.has(runId)) {
            subscribersRef.current.set(runId, new Set())
        }
        subscribersRef.current.get(runId)!.add(callback)

        return () => {
            const callbacks = subscribersRef.current.get(runId)
            if (callbacks) {
                callbacks.delete(callback)
                if (callbacks.size === 0) {
                    subscribersRef.current.delete(runId)
                }
            }
        }
    }, [])

    useEffect(() => {
        // The socket authenticates via a `token` query param (browsers can't set headers on a WS
        // upgrade). No token → no socket; a login/logout re-runs this effect and (re)connects.
        if (!token) return
        unmountedRef.current = false

        const cleanup = () => {
            if (reconnectTimerRef.current) {
                clearTimeout(reconnectTimerRef.current)
                reconnectTimerRef.current = null
            }
            if (wsRef.current) {
                wsRef.current.close()
                wsRef.current = null
            }
            retryCountRef.current = 0
            setMessages([])
            setConnected(false)
        }

        const connect = () => {
            if (unmountedRef.current) return

            const proto = window.location.protocol === 'https:' ? 'wss:' : 'ws:'
            const url = `${proto}//${window.location.host}/api/ws?token=${encodeURIComponent(token)}`
            const ws = new WebSocket(url)
            wsRef.current = ws

            ws.onopen = () => {
                setConnected(true)
                retryCountRef.current = 0
            }

            ws.onmessage = (event) => {
                try {
                    const message: WebSocketMessage = JSON.parse(event.data)
                    setMessages(prev => [...prev, message])
                    if (message.run_id) {
                        subscribersRef.current.get(message.run_id)?.forEach(callback => callback(message))
                    }
                } catch (e) {
                    console.error('Failed to parse WebSocket message:', e)
                }
            }

            ws.onclose = () => {
                // A superseded socket (replaced on remount, e.g. StrictMode) must not
                // touch shared state or reconnect — otherwise we end up with two live
                // sockets and every event is delivered twice.
                if (wsRef.current !== ws) return
                setConnected(false)
                if (!unmountedRef.current && retryCountRef.current < MAX_RETRIES) {
                    const attempt = retryCountRef.current
                    retryCountRef.current += 1
                    const delay = getBackoffDelay(attempt)
                    reconnectTimerRef.current = setTimeout(connect, delay)
                }
            }
        }

        connect()

        return () => {
            unmountedRef.current = true
            cleanup()
        }
    }, [token])

    return (
        <WebSocketContext.Provider value={{ messages, connected, subscribe }}>
            {children}
        </WebSocketContext.Provider>
    )
}

export const useWebSocket = () => {
    const context = useContext(WebSocketContext)
    if (!context) {
        throw new Error('useWebSocket must be used within WebSocketProvider')
    }
    return context
}
