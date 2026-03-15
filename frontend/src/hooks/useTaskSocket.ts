import { useState, useEffect, useRef } from 'react'
import type { WebSocketMessage } from '../types'

export function useTaskSocket(taskId: string | null) {
    const [messages, setMessages] = useState<WebSocketMessage[]>([])
    const [connected, setConnected] = useState(false)
    const [error, setError] = useState<string | null>(null)
    const [finished, setFinished] = useState(false)
    const wsRef = useRef<WebSocket | null>(null)
    const reconnectRef = useRef<boolean>(false)

    useEffect(() => {
        const cleanup = () => {
            if (wsRef.current) {
                wsRef.current.close()
                wsRef.current = null
            }
            setMessages([])
            setConnected(false)
            setError(null)
            setFinished(false)
        }

        if (!taskId) {
            cleanup()
            return
        }

        const url = `${import.meta.env.VITE_WS_URL}/api/tasks/${taskId}/ws`
        wsRef.current = new WebSocket(url)

        wsRef.current.onopen = () => {
            setConnected(true)
            setError(null)
        }

        wsRef.current.onmessage = (event) => {
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

        wsRef.current.onclose = () => {
            setConnected(false)
            if (!finished && !reconnectRef.current) {
                reconnectRef.current = true
                setTimeout(() => {
                    reconnectRef.current = false
                }, 5000)
            }
        }

        wsRef.current.onerror = () => {
            setError('WebSocket connection error')
        }

        return () => {
            cleanup()
        }
    }, [taskId])

    return { messages, connected, finished, error }
}