import { useEffect, useState } from 'react'
import { getConfig } from '../api'
import type { Command, Config } from '../types'

export function SystemTab({ send, connected }: {
  send: (command: Command) => boolean
  connected: boolean
}) {
  const [config, setConfig] = useState<Config | null>(null)
  const [text, setText] = useState('')
  const [error, setError] = useState('')

  useEffect(() => {
    let active = true
    void getConfig().then((value) => {
      if (active) setConfig(value)
    }).catch((cause: unknown) => {
      if (active) setError(cause instanceof Error ? cause.message : 'Не удалось загрузить конфигурацию')
    })
    return () => { active = false }
  }, [])

  const voice = config?.voice
  const entries = [
    ['Модель', config?.agent?.model],
    ['Effort', config?.agent?.effort],
    ['Голос', voice?.voice ?? voice?.piper_voice],
    ['Порог пробуждения', voice?.wake_threshold],
    ['Корни проектов', config?.code?.workspace_roots?.join(', ')],
    ['Браузер', config?.browser?.enabled === undefined ? undefined : config.browser.enabled ? 'Включён' : 'Выключен'],
  ]

  return (
    <div className="stack">
      <h3>Система</h3>
      {error && <div className="error-text" role="alert">{error}</div>}
      {!config && !error && <div className="muted-text">Загрузка…</div>}
      {config && <dl className="config-list card">
        {entries.map(([label, value]) => <div key={String(label)}>
          <dt>{label}</dt>
          <dd>{value === undefined || value === '' ? '—' : value}</dd>
        </div>)}
      </dl>}
      <form className="card form-grid" onSubmit={(event) => {
        event.preventDefault()
        if (text.trim() && send({ type: 'speak', text: text.trim() })) setText('')
      }}>
        <label className="field-label">Проверить голос
          <input className="field" maxLength={20000} value={text} onChange={(event) => setText(event.target.value)} placeholder="Текст для озвучивания" />
        </label>
        <button className="button" type="submit" disabled={!connected || !text.trim()}>Произнести</button>
      </form>
    </div>
  )
}
