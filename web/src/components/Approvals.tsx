import { useEffect, useState } from 'react'
import type { Approval, Command } from '../types'

function ApprovalCard({ approval, send, connected }: {
  approval: Approval
  send: (command: Command) => boolean
  connected: boolean
}) {
  const [expanded, setExpanded] = useState(false)
  const [seconds, setSeconds] = useState(0)
  useEffect(() => {
    function update() {
      const since = approval.ts ?? approval.created_at
      setSeconds(since ? Math.max(0, Math.floor((Date.now() - new Date(since).getTime()) / 1000)) : 0)
    }
    update()
    const timer = setInterval(update, 1000)
    return () => clearInterval(timer)
  }, [approval.ts, approval.created_at])

  const id = approval.approval_id ?? approval.id
  const long = approval.detail.split('\n').length > 12
  const source = approval.source.startsWith('code:') ? `Задача кода · ${approval.source.slice(5)}` : 'Основной разговор'
  const elapsed = `${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, '0')}`

  function answer(allow: boolean, remember: boolean) {
    if (id) send({ type: 'approve', approval_id: id, allow, remember })
  }

  return (
    <article className="card approval-card">
      <h3>{approval.title}</h3>
      <div className="card-meta">{source} · {elapsed}</div>
      <pre className={`detail-block${!expanded && long ? ' clamped' : ''}`}>{approval.detail}</pre>
      {long && <button className="text-button" type="button" onClick={() => setExpanded(!expanded)}>{expanded ? 'Свернуть' : 'Показать всё'}</button>}
      <div className="card-actions">
        <button className="button compact primary" type="button" disabled={!connected || !id} onClick={() => answer(true, false)}>Разрешить</button>
        <button className="button compact" type="button" disabled={!connected || !id} onClick={() => answer(true, true)}>Разрешить для сессии</button>
        <button className="button compact danger" type="button" disabled={!connected || !id} onClick={() => answer(false, false)}>Отклонить</button>
      </div>
    </article>
  )
}

export function Approvals({ approvals, send, connected }: {
  approvals: Approval[]
  send: (command: Command) => boolean
  connected: boolean
}) {
  if (!approvals.length) return <div className="empty-state">Подтверждений нет</div>
  return (
    <div className="stack">
      {approvals.map((approval) => <ApprovalCard key={approval.approval_id ?? approval.id} approval={approval} send={send} connected={connected} />)}
    </div>
  )
}
