import React, { createContext, useContext, useEffect, useState, useRef, useCallback } from 'react'
import type { WebSocketMessage } from '../types'

interface WebSocketContextValue {
    messages: WebSocketMessage[]
    connected: boolean
    error: string | null
    subscribe: (taskId: string, callback: (message: WebSocketMessage) => void) => () => void
}

const WebSocketContext = createContext<WebSocketContextValue | null>(null)

const BASE_DELAY_MS = 1000
const MAX_DELAY_MS = 30000
const MAX_RETRIES = 10

function getBackoffDelay(attempt: number): number {
    return Math.min(BASE_DELAY_MS * 2 ** attempt, MAX_DELAY_MS)
}

export const WebSocketProvider: React.FC<{ children: React.ReactNode }> = ({ children }) => {
    const [messages, setMessages] = useState<WebSocketMessage[]>([])
    const [connected, setConnected] = useState(false)
    const [error, setError] = useState<string | null>(null)
    const wsRef = useRef<WebSocket | null>(null)
    const retryCountRef = useRef(0)
    const reconnectTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null)
    const unmountedRef = useRef(false)
    const subscribersRef = useRef<Map<string, Set<(message: WebSocketMessage) => void>>>(new Map())

    const subscribe = useCallback((taskId: string, callback: (message: WebSocketMessage) => void) => {
        if (!subscribersRef.current.has(taskId)) {
            subscribersRef.current.set(taskId, new Set())
        }
        subscribersRef.current.get(taskId)!.add(callback)

        // Return unsubscribe function
        return () => {
            const callbacks = subscribersRef.current.get(taskId)
            if (callbacks) {
                callbacks.delete(callback)
                if (callbacks.size === 0) {
                    subscribersRef.current.delete(taskId)
                }
            }
        }
    }, [])

    useEffect(() => {
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
            setError(null)
        }

        const connect = () => {
            if (unmountedRef.current) return

            const url = `${import.meta.env.VITE_WS_URL}/api/ws`
            const ws = new WebSocket(url)
            wsRef.current = ws

            ws.onopen = () => {
                setConnected(true)
                setError(null)
                retryCountRef.current = 0
            }

            ws.onmessage = (event) => {
                try {
                    const message: WebSocketMessage = JSON.parse(event.data)
                    setMessages(prev => [...prev, message])

                    // Notify subscribers keyed by run_id (builds) or task_id (legacy).
                    const key = message.run_id ?? message.task_id
                    if (key) {
                        const callbacks = subscribersRef.current.get(key)
                        if (callbacks) {
                            callbacks.forEach(callback => callback(message))
                        }
                    }
                } catch (e) {
                    console.error('Failed to parse WebSocket message:', e)
                }
            }

            ws.onclose = () => {
                setConnected(false)
                if (!unmountedRef.current && retryCountRef.current < MAX_RETRIES) {
                    const attempt = retryCountRef.current
                    retryCountRef.current += 1
                    const delay = getBackoffDelay(attempt)
                    reconnectTimerRef.current = setTimeout(connect, delay)
                }
            }

            ws.onerror = () => {
                setError('WebSocket connection error')
            }
        }

        connect()

        return () => {
            unmountedRef.current = true
            cleanup()
        }
    }, [])

    return (
        <WebSocketContext.Provider value={{ messages, connected, error, subscribe }}>
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

export const useTaskWebSocket = (taskId: string | null) => {
    const { subscribe } = useWebSocket()
    const [messages, setMessages] = useState<WebSocketMessage[]>([])
    const [finished, setFinished] = useState(false)

    useEffect(() => {
        if (!taskId) {
            setMessages([])
            setFinished(false)
            return
        }

        setMessages([])
        setFinished(false)

        const unsubscribe = subscribe(taskId, (message) => {
            setMessages(prev => [...prev, message])

            if (message.type === 'task_completed' || message.type === 'task_failed') {
                setFinished(true)
            }
        })

        return unsubscribe
    }, [taskId, subscribe])

    return { messages, finished }
}
