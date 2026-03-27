import { useState, useEffect, useRef } from 'react'
import type { WebSocketMessage } from '../types'

const BASE_DELAY_MS = 1000
const MAX_DELAY_MS = 30000
const MAX_RETRIES = 10

function getBackoffDelay(attempt: number): number {
    return Math.min(BASE_DELAY_MS * 2 ** attempt, MAX_DELAY_MS)
}

export function useTaskSocket(taskId: string | null) {
    const [messages, setMessages] = useState<WebSocketMessage[]>([])
    const [connected, setConnected] = useState(false)
    const [error, setError] = useState<string | null>(null)
    const [finished, setFinished] = useState(false)
    const wsRef = useRef<WebSocket | null>(null)
    const retryCountRef = useRef(0)
    const reconnectTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null)
    const finishedRef = useRef(false)
    const unmountedRef = useRef(false)

    useEffect(() => {
        finishedRef.current = finished
    }, [finished])

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
            setFinished(false)
        }

        if (!taskId) {
            cleanup()
            return
        }

        const connect = () => {
            if (unmountedRef.current) return

            const url = `${import.meta.env.VITE_WS_URL}/api/tasks/${taskId}/ws`
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

                    if (message.type === 'task_completed' || message.type === 'task_failed') {
                        setFinished(true)
                    }
                } catch (e) {
                    console.error('Failed to parse WebSocket message:', e)
                }
            }

            ws.onclose = () => {
                setConnected(false)
                if (!finishedRef.current && !unmountedRef.current && retryCountRef.current < MAX_RETRIES) {
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
    }, [taskId])

    return { messages, connected, finished, error }
}