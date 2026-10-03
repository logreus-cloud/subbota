import { useEffect, useState } from 'react'
import type { Approval, CodeTask, Command, Reminder } from '../types'
import { Approvals } from './Approvals'
import { Reminders } from './Reminders'
import { CodeTasks } from './CodeTasks'
import { Memory } from './Memory'
import { SystemTab } from './SystemTab'

type Tab = 'approvals' | 'reminders' | 'code' | 'memory' | 'system'
const tabs: { id: Tab; label: string }[] = [
  { id: 'approvals', label: 'Подтверждения' },
  { id: 'reminders', label: 'Напоминания' },
  { id: 'code', label: 'Задачи кода' },
  { id: 'memory', label: 'Память' },
  { id: 'system', label: 'Система' },
]

export function SidePanel({ open, onClose, approvals, reminders, codeTasks, send, connected }: {
  open: boolean
  onClose: () => void
  approvals: Approval[]
  reminders: Reminder[]
  codeTasks: CodeTask[]
  send: (command: Command) => boolean
  connected: boolean
}) {
  const [tab, setTab] = useState<Tab>('approvals')
  const [previousCount, setPreviousCount] = useState(approvals.length)
  if (approvals.length > previousCount) {
    setPreviousCount(approvals.length)
    setTab('approvals')
  } else if (approvals.length < previousCount) {
    setPreviousCount(approvals.length)
  }

  useEffect(() => {
    if (!open) return
    function onKey(event: KeyboardEvent) {
      if (event.key === 'Escape') onClose()
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [open, onClose])

  return (
    <aside className={`side-column${open ? ' open' : ''}`} aria-label="Панель Джарвиса">
      <div className="side-header">
        <h2>Панель</h2>
        <button className="button compact side-close" type="button" onClick={onClose}>Закрыть</button>
      </div>
      <nav className="tabs" aria-label="Разделы панели">
        {tabs.map((item) => <button
          className={`tab${tab === item.id ? ' active' : ''}`}
          type="button"
          aria-current={tab === item.id ? 'page' : undefined}
          key={item.id}
          onClick={() => setTab(item.id)}
        >
          {item.label}{item.id === 'approvals' && approvals.length > 0 && <span className="badge">{approvals.length}</span>}
        </button>)}
      </nav>
      <div className="side-content">
        {tab === 'approvals' && <Approvals approvals={approvals} send={send} connected={connected} />}
        {tab === 'reminders' && <Reminders reminders={reminders} />}
        {tab === 'code' && <CodeTasks tasks={codeTasks} />}
        {tab === 'memory' && <Memory />}
        {tab === 'system' && <SystemTab send={send} connected={connected} />}
      </div>
    </aside>
  )
}
