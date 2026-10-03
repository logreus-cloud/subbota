import { useEffect, useState } from 'react'
import { getMemory, putMemory } from '../api'

export function Memory() {
  const [text, setText] = useState('')
  const [saved, setSaved] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')

  useEffect(() => {
    let active = true
    void getMemory().then((memory) => {
      if (active) {
        setText(memory.text)
        setSaved(memory.text)
      }
    }).catch((cause: unknown) => {
      if (active) setError(cause instanceof Error ? cause.message : 'Не удалось загрузить память')
    })
    return () => { active = false }
  }, [])

  async function save() {
    setBusy(true)
    setError('')
    try {
      const memory = await putMemory(text)
      setSaved(memory.text)
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : 'Не удалось сохранить память')
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="stack">
      <div className="row-between">
        <h3>Память</h3>
        {text !== saved && <span className="chip">Изменено</span>}
      </div>
      <textarea
        className="field memory-editor"
        aria-label="Содержимое памяти"
        maxLength={50000}
        value={text}
        onChange={(event) => setText(event.target.value)}
      />
      {error && <div className="error-text" role="alert">{error}</div>}
      <button className="button primary" type="button" disabled={busy || text === saved} onClick={() => void save()}>
        {busy ? 'Сохранение…' : 'Сохранить'}
      </button>
    </div>
  )
}
