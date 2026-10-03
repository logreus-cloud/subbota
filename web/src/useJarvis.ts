import { useCallback, useEffect, useReducer, useRef, useState } from 'react'
import { getHistory, getReminders } from './api'
import type { Approval, CodeTask, Command, Event, JarvisState, Snapshot } from './types'

const initialState: JarvisState = {
  status: 'idle',
  mic_muted: false,
  events: [],
  approvals: [],
  reminders: [],
  code_tasks: [],
  connected: false,
}

type Action =
  | { type: 'snapshot'; data: Snapshot }
  | { type: 'event'; data: Event }
  | { type: 'connected'; value: boolean }
  | { type: 'older'; events: Event[] }
  | { type: 'reminders'; reminders: JarvisState['reminders'] }

function eventKey(event: Event) {
  return event.id
}

function mergeEvents(a: Event[], b: Event[]) {
  const events = new Map<number, Event>()
  for (const event of [...a, ...b]) {
    if (event.id > 0) events.set(eventKey(event), event)
  }
  return [...events.values()].sort((left, right) => left.id - right.id)
}

function approvalId(approval: Approval) {
  return approval.approval_id ?? approval.id
}

function taskId(task: CodeTask) {
  return task.task_id ?? task.id
}

function reducer(state: JarvisState, action: Action): JarvisState {
  if (action.type === 'connected') return { ...state, connected: action.value }
  if (action.type === 'reminders') return { ...state, reminders: action.reminders }
  if (action.type === 'older') return { ...state, events: mergeEvents(action.events, state.events) }
  if (action.type === 'snapshot') {
    const history = mergeEvents([], action.data.history)
    const resetIndex = history.findLastIndex((event) => event.type === 'session_reset')
    return {
      ...state,
      status: action.data.state.status,
      mic_muted: action.data.state.mic_muted,
      events: resetIndex < 0 ? history : history.slice(resetIndex + 1),
      approvals: action.data.approvals,
      reminders: action.data.reminders,
      code_tasks: action.data.code_tasks,
      connected: true,
    }
  }
  const event = action.data
  const next = { ...state }
  if (event.type === 'status') {
    next.status = event.status
    next.mic_muted = event.mic_muted
  }
  if (event.type === 'session_reset') {
    next.events = []
    return next
  }
  if (event.type === 'approval_request') {
    next.approvals = [
      ...state.approvals.filter((item) => approvalId(item) !== event.approval_id),
      { ...event, id: event.approval_id },
    ]
  }
  if (event.type === 'approval_resolved') {
    next.approvals = state.approvals.filter((item) => approvalId(item) !== event.approval_id)
  }
  if (event.type === 'code_task') {
    next.code_tasks = [
      ...state.code_tasks.filter((item) => taskId(item) !== event.task_id),
      { ...event, id: event.task_id },
    ]
  }
  if (event.id > 0) next.events = mergeEvents(state.events, [event])
  return next
}

export function useJarvis() {
  const [state, dispatch] = useReducer(reducer, initialState)
  const [loadingOlder, setLoadingOlder] = useState(false)
  const [hasMore, setHasMore] = useState(true)
  const socket = useRef<WebSocket | null>(null)
  const loading = useRef(false)

  useEffect(() => {
    let stopped = false
    let retry: ReturnType<typeof setTimeout> | undefined
    let attempts = 0
    const delays = [1000, 2000, 5000, 10000]
    const url = `${location.protocol === 'https:' ? 'wss' : 'ws'}://${location.host}/ws`

    function connect() {
      const ws = new WebSocket(url)
      socket.current = ws
      ws.onopen = () => {
        attempts = 0
        dispatch({ type: 'connected', value: true })
      }
      ws.onmessage = (message: MessageEvent<string>) => {
        try {
          const data: unknown = JSON.parse(message.data)
          if (!data || typeof data !== 'object' || !('type' in data)) return
          if (data.type === 'snapshot') {
            const snapshot = data as Snapshot
            dispatch({ type: 'snapshot', data: snapshot })
            setHasMore(snapshot.history.length >= 300)
          } else {
            const event = data as Event
            dispatch({ type: 'event', data: event })
            if (event.type === 'reminders_changed') {
              void getReminders().then((reminders) => {
                if (!stopped) dispatch({ type: 'reminders', reminders })
              }).catch(() => {})
            }
          }
        } catch {
          // Повреждённое сообщение не должно прерывать соединение.
        }
      }
      ws.onclose = () => {
        if (socket.current === ws) socket.current = null
        dispatch({ type: 'connected', value: false })
        if (!stopped) {
          retry = setTimeout(connect, delays[Math.min(attempts, delays.length - 1)])
          attempts += 1
        }
      }
    }
    connect()
    return () => {
      stopped = true
      clearTimeout(retry)
      socket.current?.close()
      socket.current = null
    }
  }, [])

  const send = useCallback((command: Command) => {
    if (socket.current?.readyState !== WebSocket.OPEN) return false
    socket.current.send(JSON.stringify(command))
    return true
  }, [])

  const loadOlder = useCallback(async () => {
    const before = state.events[0]?.id
    if (!before || loading.current || !hasMore) return
    loading.current = true
    setLoadingOlder(true)
    try {
      const events = await getHistory(before)
      dispatch({ type: 'older', events })
      setHasMore(events.length === 200)
    } finally {
      loading.current = false
      setLoadingOlder(false)
    }
  }, [state.events, hasMore])

  return { state, send, loadOlder, loadingOlder, hasMore }
}
