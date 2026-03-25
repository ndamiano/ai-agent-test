import axios from 'axios'
import type { Task, TaskDetail, AskResponse, SystemStatus, Settings } from '../types'

const base = axios.create({ baseURL: '/api' })

export const api = {
    // Tasks
    createTask: (goal: string, execution_mode?: string) =>
        base.post<Task>('/tasks', { goal, execution_mode }).then(r => r.data),
    listTasks: (status?: string) =>
        base.get<Task[]>('/tasks', { params: { status } }).then(r => r.data),
    getTask: (id: string) =>
        base.get<TaskDetail>(`/tasks/${id}`).then(r => r.data),
    getContextValue: (task_id: string, key: string) =>
        base.get<string>(`/tasks/${task_id}/context/${key}`).then(r => r.data),
    askTask: (task_id: string, question: string) =>
        base.post<AskResponse>(`/tasks/${task_id}/ask`, { question }).then(r => r.data),
    deleteTask: (id: string) =>
        base.delete(`/tasks/${id}`),

    // System
    getStatus: () =>
        base.get<SystemStatus>('/system/status').then(r => r.data),

    // Settings
    getSettings: () =>
        base.get<Settings>('/settings').then(r => r.data),
    updateSettings: (settings: Settings) =>
        base.put<Settings>('/settings', settings).then(r => r.data),
}