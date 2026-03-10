import { useState, useEffect } from 'react'
import { api } from '../api/client'
import type { Task } from '../types'

export function useTasks() {
    const [tasks, setTasks] = useState<Task[]>([])
    const [loading, setLoading] = useState(false)
    const [error, setError] = useState<string | null>(null)

    const refresh = async () => {
        setLoading(true)
        setError(null)
        try {
            const fetchedTasks = await api.listTasks()
            setTasks(fetchedTasks)
        } catch (e) {
            setError(e instanceof Error ? e.message : 'Failed to fetch tasks')
        } finally {
            setLoading(false)
        }
    }

    useEffect(() => {
        refresh()
    }, [])

    return { tasks, loading, error, refresh }
}