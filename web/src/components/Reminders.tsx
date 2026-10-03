import { useEffect, useState } from 'react'
import { addReminder, deleteReminder, getReminders } from '../api'
import type { Reminder } from '../types'

function nextRun(reminder: Reminder) {
  if (reminder.recurrence) return `Повтор: ${reminder.recurrence}`
  const raw = reminder.next_run ?? reminder.run_at
  if (!raw) return 'Время не указано'
  const date = new Date(raw)
  if (Number.isNaN(date.getTime())) return raw
  const now = new Date()
  const today = new Date(now.getFullYear(), now.getMonth(), now.getDate())
  const day = new Date(date.getFullYear(), date.getMonth(), date.getDate())
  const distance = Math.round((day.getTime() - today.getTime()) / 86400000)
  const time = date.toLocaleTimeString('ru-RU', { hour: '2-digit', minute: '2-digit' })
  if (distance === 0) return `сегодня в ${time}`
  if (distance === 1) return `завтра в ${time}`
  if (distance > 1 && distance < 7) return `${date.toLocaleDateString('ru-RU', { weekday: 'short' })} ${time}`
  return date.toLocaleString('ru-RU', { day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit' })
}

export function Reminders({ reminders }: { reminders: Reminder[] }) {
  const [list, setList] = useState(reminders)
  const [text, setText] = useState('')
  const [kind, setKind] = useState<'say' | 'agent'>('say')
  const [schedule, setSchedule] = useState<'once' | 'cron'>('once')
  const [when, setWhen] = useState('')
  const [cron, setCron] = useState('')
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)

  useEffect(() => { setList(reminders) }, [reminders])

  async function refresh() {
    setList(await getReminders())
  }

  async function submit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault()
    setError('')
    setBusy(true)
    try {
      await addReminder({
        text: text.trim(),
        kind,
        when: schedule === 'once' && when ? new Date(when).toISOString() : null,
        recurrence: schedule === 'cron' ? cron.trim() : null,
      })
      setText('')
      setWhen('')
      setCron('')
      await refresh()
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : 'Не удалось создать напоминание')
    } finally {
      setBusy(false)
    }
  }

  async function remove(id: number) {
    setError('')
    try {
      const response = await deleteReminder(id)
      if (!response.cancelled) throw new Error('Напоминание уже удалено')
      await refresh()
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : 'Не удалось удалить напоминание')
    }
  }

  return (
    <div className="stack">
      <h3>Напоминания</h3>
      {list.length === 0 ? <div className="empty-state">Напоминаний нет</div> : list.map((reminder) => (
        <article className="card" key={reminder.id}>
          <div className="row-between"><strong>{reminder.text}</strong><button className="text-button" type="button" onClick={() => void remove(reminder.id)}>Удалить</button></div>
          <div className="card-meta">{reminder.kind === 'agent' ? 'Задача' : 'Сказать'} · {nextRun(reminder)}</div>
        </article>
      ))}
      <form className="card form-grid" onSubmit={(event) => void submit(event)}>
        <h3>Добавить</h3>
        <label className="field-label">Текст
          <textarea className="field" rows={3} maxLength={20000} required value={text} onChange={(event) => setText(event.target.value)} />
        </label>
        <label className="field-label">Тип
          <select className="field" value={kind} onChange={(event) => setKind(event.target.value as 'say' | 'agent')}>
            <option value="say">Сказать</option>
            <option value="agent">Задача</option>
          </select>
        </label>
        <label className="field-label">Расписание
          <select className="field" value={schedule} onChange={(event) => setSchedule(event.target.value as 'once' | 'cron')}>
            <option value="once">Дата и время</option>
            <option value="cron">Повтор по cron</option>
          </select>
        </label>
        {schedule === 'once'
          ? <label className="field-label">Когда<input className="field" type="datetime-local" required value={when} onChange={(event) => setWhen(event.target.value)} /></label>
          : <label className="field-label">Cron<input className="field" required placeholder="0 9 * * 1-5" value={cron} onChange={(event) => setCron(event.target.value)} /></label>}
        {error && <div className="error-text" role="alert">{error}</div>}
        <button className="button primary" type="submit" disabled={busy}>{busy ? 'Сохранение…' : 'Добавить'}</button>
      </form>
    </div>
  )
}
