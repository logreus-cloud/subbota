export type Status = 'idle' | 'listening' | 'recording' | 'transcribing' | 'thinking' | 'speaking'

export type Approval = {
  id?: string
  approval_id?: string
  tool: string
  title: string
  detail: string
  source: string
  ts?: string
  created_at?: string
}

export type Reminder = {
  id: number
  text: string
  kind: 'say' | 'agent'
  run_at?: string | null
  recurrence?: string | null
  next_run?: string | null
}

export type CodeTask = {
  id?: string
  task_id?: string
  project: string
  task: string
  status: 'running' | 'done' | 'failed' | 'cancelled'
  summary?: string
  ts?: string
  started_at?: string
  created_at?: string
}

export type Event = { id: number; ts: string } & (
  | { type: 'status'; status: Status; mic_muted: boolean }
  | { type: 'user_message'; turn_id: string; text: string; source: 'voice' | 'text' | 'scheduler' | 'system' }
  | { type: 'assistant_message'; turn_id: string; text: string }
  | { type: 'assistant_delta'; turn_id?: string; text?: string }
  | { type: 'tool_call'; turn_id: string; tool_use_id: string; name: string; input: Record<string, unknown> }
  | { type: 'tool_result'; turn_id: string; tool_use_id: string; is_error: boolean; text: string }
  | { type: 'turn_done'; turn_id: string; result: string; is_error: boolean; cost_usd: number | null; duration_ms: number }
  | { type: 'approval_request'; approval_id: string; tool: string; title: string; detail: string; source: string }
  | { type: 'approval_resolved'; approval_id: string; allowed: boolean; by: 'ui' | 'voice' | 'timeout' | 'session_rule' }
  | { type: 'announcement'; text: string; kind: 'info' | 'reminder' | 'code_task' | 'error' }
  | { type: 'reminders_changed' }
  | { type: 'code_task'; task_id: string; project: string; task: string; status: CodeTask['status']; summary: string }
  | { type: 'session_reset' }
  | { type: 'error'; message: string }
)

export type Snapshot = {
  type: 'snapshot'
  state: { status: Status; mic_muted: boolean }
  history: Event[]
  /** id последнего session_reset в базе */
  reset_floor?: number
  approvals: Approval[]
  reminders: Reminder[]
  code_tasks: CodeTask[]
}

export type Command =
  | { type: 'chat'; text: string }
  | { type: 'approve'; approval_id: string; allow: boolean; remember: boolean }
  | { type: 'interrupt' }
  | { type: 'reset' }
  | { type: 'mute'; muted: boolean }
  | { type: 'speak'; text: string }

export type Config = {
  agent?: { model?: string; effort?: string }
  voice?: { voice?: string; piper_voice?: string; wake_threshold?: number }
  code?: { workspace_roots?: string[] }
  browser?: { enabled?: boolean }
}

export type SubbotaState = {
  status: Status
  mic_muted: boolean
  events: Event[]
  approvals: Approval[]
  reminders: Reminder[]
  code_tasks: CodeTask[]
  connected: boolean
  /** id последнего session_reset: старее него историю не подгружаем */
  floor: number
}
