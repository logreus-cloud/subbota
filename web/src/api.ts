import type { CodeTask, Config, Event, Reminder } from './types'

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(path, init)
  if (!response.ok) {
    const body: unknown = await response.json().catch(() => null)
    const detail = body && typeof body === 'object' && 'detail' in body ? body.detail : null
    throw new Error(typeof detail === 'string' ? detail : `Ошибка сервера: ${response.status}`)
  }
  return response.json() as Promise<T>
}

const json = (method: string, body: unknown): RequestInit => ({
  method,
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify(body),
})

export const getHistory = (before: number, limit = 200) =>
  request<Event[]>(`/api/history?before=${before}&limit=${limit}`)

export const getReminders = () => request<Reminder[]>('/api/reminders')

export const addReminder = (body: {
  text: string
  when: string | null
  recurrence?: string | null
  kind: 'say' | 'agent'
}) => request<Reminder>('/api/reminders', json('POST', body))

export const deleteReminder = (id: number) =>
  request<{ cancelled: boolean }>(`/api/reminders/${id}`, { method: 'DELETE' })

export const getMemory = () => request<{ text: string }>('/api/memory')

export const putMemory = (text: string) =>
  request<{ text: string }>('/api/memory', json('PUT', { text }))

export const getCodeTasks = () => request<CodeTask[]>('/api/code-tasks')

export const cancelCodeTask = (id: string) =>
  request<{ cancelled: boolean }>(`/api/code-tasks/${encodeURIComponent(id)}/cancel`, { method: 'POST' })

export const getConfig = () => request<Config>('/api/config')
