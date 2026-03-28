import type { Task, TaskDetail, AskResponse, SystemStatus, Settings } from '../types'

const base = '/api'

async function request<T>(path: string, init?: RequestInit): Promise<T> {
    const res = await fetch(`${base}${path}`, {
        headers: { 'Content-Type': 'application/json' },
        ...init,
    })
    if (!res.ok) throw new Error(`${res.status}`)
    return res.json() as Promise<T>
}

export const api = {
    // Tasks
    createTask: (goal: string, execution_mode?: string) =>
        request<Task>('/tasks', { method: 'POST', body: JSON.stringify({ goal, execution_mode }) }),
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