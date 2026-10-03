import { useRef, useState } from 'react'
import type { Command } from '../types'

export function Composer({ connected, send }: {
  connected: boolean
  send: (command: Command) => boolean
}) {
  const [text, setText] = useState('')
  const input = useRef<HTMLTextAreaElement>(null)

  function submit() {
    const value = text.trim()
    if (!value || !send({ type: 'chat', text: value })) return
    setText('')
    if (input.current) input.current.style.height = 'auto'
    input.current?.focus()
  }

  return (
    <form className="composer" onSubmit={(event) => {
      event.preventDefault()
      submit()
    }}>
      <div className="composer-inner">
        <textarea
          ref={input}
          value={text}
          rows={1}
          maxLength={20000}
          placeholder={connected ? 'Напишите Субботе…' : 'Ожидание соединения…'}
          aria-label="Сообщение"
          onChange={(event) => {
            setText(event.target.value)
            event.target.style.height = 'auto'
            event.target.style.height = `${Math.min(event.target.scrollHeight, 190)}px`
          }}
          onKeyDown={(event) => {
            if (event.key === 'Enter' && !event.shiftKey && !event.nativeEvent.isComposing) {
              event.preventDefault()
              submit()
            }
          }}
        />
        <button className="button primary" type="submit" disabled={!connected || !text.trim()}>Отправить</button>
      </div>
    </form>
  )
}
