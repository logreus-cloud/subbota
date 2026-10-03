import { useState } from 'react'
import type { Command, Event, Status } from '../types'

const labels: Record<Status, string> = {
  idle: 'Скажите «Суббота»',
  listening: 'Слушаю',
  recording: 'Слушаю',
  transcribing: 'Распознаю',
  thinking: 'Думаю',
  speaking: 'Говорю',
}

type Props = {
  status: Status
  muted: boolean
  connected: boolean
  events: Event[]
  send: (command: Command) => boolean
}

type ActionsProps = Pick<Props, 'status' | 'muted' | 'connected' | 'send'>

export function CoreActions({ status, muted, connected, send }: ActionsProps) {
  const [confirmReset, setConfirmReset] = useState(false)
  return (
    <div className="core-actions">
      <button className="button" type="button" disabled={!connected} onClick={() => send({ type: 'mute', muted: !muted })}>
        {muted ? 'Включить микрофон' : 'Выключить микрофон'}
      </button>
      <button className="button" type="button" disabled={!connected || !['thinking', 'speaking'].includes(status)} onClick={() => send({ type: 'interrupt' })}>
        Прервать
      </button>
      <button className={`button${confirmReset ? ' danger' : ''}`} type="button" disabled={!connected} onClick={() => {
        if (confirmReset) {
          send({ type: 'reset' })
          setConfirmReset(false)
        } else {
          setConfirmReset(true)
        }
      }}>
        {confirmReset ? 'Точно?' : 'Новый разговор'}
      </button>
      {confirmReset && <button className="text-button" type="button" onClick={() => setConfirmReset(false)}>Отмена</button>}
    </div>
  )
}

export function Core({ status, muted, connected, events, send }: Props) {
  const cost = events.reduce((total, event) => total + (event.type === 'turn_done' ? event.cost_usd ?? 0 : 0), 0)

  return (
    <aside className="core-column">
      <div className="logo">СУББОТА</div>
      <div className="logo-caption">ПЕРСОНАЛЬНЫЙ АССИСТЕНТ</div>
      <div className="reactor-wrap">
        <svg className="reactor" viewBox="0 0 200 200" aria-hidden="true">
          <circle cx="100" cy="100" r="91" fill="none" stroke="currentColor" strokeOpacity=".22" />
          <circle className="reactor-ring" cx="100" cy="100" r="82" fill="none" stroke="currentColor" strokeWidth="2" strokeDasharray="58 9 23 9 72 9 23 9" />
          <circle cx="100" cy="100" r="68" fill="none" stroke="currentColor" strokeOpacity=".28" strokeWidth="7" />
          <circle className="reactor-ring reverse" cx="100" cy="100" r="57" fill="none" stroke="currentColor" strokeWidth="3" strokeDasharray="32 12 73 12" />
          <circle cx="100" cy="100" r="42" fill="currentColor" fillOpacity=".08" stroke="currentColor" strokeWidth="2" />
          <circle cx="100" cy="100" r="27" fill="currentColor" fillOpacity=".14" stroke="currentColor" strokeOpacity=".55" />
          <circle cx="100" cy="100" r="9" fill="currentColor" />
        </svg>
      </div>
      <div className="core-status" role="status">{muted ? 'Микрофон выключен' : labels[status]}</div>
      <div className="connection">
        <span className={`connection-dot${connected ? ' online' : ''}`} />
        {connected ? 'Сервер подключён' : 'Нет соединения с сервером'}
      </div>
      <CoreActions status={status} muted={muted} connected={connected} send={send} />
      <div className="session-cost"><span>Стоимость сессии</span><strong>${cost.toFixed(4)}</strong></div>
    </aside>
  )
}
