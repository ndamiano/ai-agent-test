import type { Task, TaskDetail, AskResponse, SystemStatus, Settings } from '../types'

const base = '/api'

async function sleep(ms: number): Promise<void> {
    return new Promise(resolve => setTimeout(resolve, ms))
}

async function request<T>(path: string, init?: RequestInit, retries = 2): Promise<T> {
    for (let attempt = 0; attempt <= retries; attempt++) {
        const res = await fetch(`${base}${path}`, {
            headers: { 'Content-Type': 'application/json' },
            ...init,
        })

        // Handle rate limiting with exponential backoff
        if (res.status === 429 && attempt < retries) {
            const retryAfter = res.headers.get('Retry-After')
            const waitTime = retryAfter ? parseInt(retryAfter) * 1000 : Math.pow(2, attempt) * 500
            console.warn(`Rate limited, retrying after ${waitTime}ms (attempt ${attempt + 1}/${retries})`)
            await sleep(waitTime)
            continue
        }

        if (!res.ok) {
            // Provide better error messages
            if (res.status === 429) {
                throw new Error('Rate limit exceeded. Please try again in a moment.')
            }
            throw new Error(`${res.status}`)
        }

        return res.json() as Promise<T>
    }

    throw new Error('Rate limit exceeded after retries')
}

export const api = {
    // Tasks
    createTask: (goal: string, working_directory?: string) =>
        request<Task>('/tasks', { method: 'POST', body: JSON.stringify({ goal, working_directory }) }),
    listTasks: (status?: string) =>
        request<Task[]>(status ? `/tasks?status=${encodeURIComponent(status)}` : '/tasks'),
    getTask: (id: string) =>
        request<TaskDetail>(`/tasks/${id}`),
    getContextValue: (task_id: string, key: string) =>
        request<string>(`/tasks/${task_id}/context/${key}`),
    askTask: (task_id: string, question: string) =>
        request<AskResponse>(`/tasks/${task_id}/ask`, { method: 'POST', body: JSON.stringify({ question }) }),
    deleteTask: (id: string) =>
        fetch(`${base}/tasks/${id}`, { method: 'DELETE' }),
    retryTask: (id: string) =>
        request<Task>(`/tasks/${id}/retry`, { method: 'POST' }),

    // System
    getStatus: () =>
        request<SystemStatus>('/system/status'),

    // Settings
    getSettings: () =>
        request<Settings>('/settings'),
    updateSettings: (settings: Settings) =>
        request<Settings>('/settings', { method: 'PUT', body: JSON.stringify(settings) }),
}