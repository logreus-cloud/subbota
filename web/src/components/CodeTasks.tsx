import { useState } from 'react'
import { cancelCodeTask } from '../api'
import type { CodeTask } from '../types'
import { Markdown } from './TurnView'

const statuses: Record<CodeTask['status'], string> = {
  running: 'В работе',
  done: 'Готово',
  failed: 'Ошибка',
  cancelled: 'Отменена',
}

function TaskCard({ task }: { task: CodeTask }) {
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const id = task.task_id ?? task.id
  const time = task.started_at ?? task.created_at ?? task.ts

  async function cancel() {
    if (!id) return
    setBusy(true)
    setError('')
    try {
      const result = await cancelCodeTask(id)
      if (!result.cancelled) throw new Error('Задача уже завершена')
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : 'Не удалось отменить задачу')
    } finally {
      setBusy(false)
    }
  }

  return (
    <article className="card">
      <div className="row-between">
        <h3>{task.project}</h3>
        <span className={`chip ${task.status}`}>{statuses[task.status]}</span>
      </div>
      <p>{task.task}</p>
      {time && <div className="card-meta">{new Date(time).toLocaleString('ru-RU')}</div>}
      {task.summary && <Markdown text={task.summary} />}
      {task.status === 'running' && <button className="button compact danger" type="button" disabled={busy || !id} onClick={() => void cancel()}>Отменить</button>}
      {error && <div className="error-text" role="alert">{error}</div>}
    </article>
  )
}

export function CodeTasks({ tasks }: { tasks: CodeTask[] }) {
  if (tasks.length === 0) return <div className="empty-state">Задач кода нет</div>
  return <div className="stack">{tasks.map((task) => <TaskCard key={task.task_id ?? task.id} task={task} />)}</div>
}
