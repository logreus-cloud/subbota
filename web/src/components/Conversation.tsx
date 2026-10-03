import { useCallback, useEffect, useRef, useState } from 'react'
import type { Command, Event } from '../types'
import { Composer } from './Composer'
import { TurnView } from './TurnView'

type Item = { kind: 'turn'; id: string; events: Event[] } | { kind: 'announcement'; event: Event }

function groupEvents(events: Event[]): Item[] {
  const items: Item[] = []
  const turns = new Map<string, Extract<Item, { kind: 'turn' }>>()
  for (const event of events) {
    if (event.type === 'announcement' || event.type === 'error') {
      items.push({ kind: 'announcement', event })
    } else if ('turn_id' in event && event.turn_id === 'system') {
      // Фоновые сообщения группируем по соседству, а не в один «ход» на всё время.
      const last = items.at(-1)
      if (last?.kind === 'turn' && last.id.startsWith('system-')) last.events.push(event)
      else items.push({ kind: 'turn', id: `system-${event.id}`, events: [event] })
    } else if ('turn_id' in event && event.turn_id) {
      let turn = turns.get(event.turn_id)
      if (!turn) {
        turn = { kind: 'turn', id: event.turn_id, events: [] }
        turns.set(event.turn_id, turn)
        items.push(turn)
      }
      turn.events.push(event)
    }
  }
  return items
}

type Props = {
  events: Event[]
  connected: boolean
  send: (command: Command) => boolean
  loadOlder: () => Promise<void>
  loadingOlder: boolean
  hasMore: boolean
}

export function Conversation({ events, connected, send, loadOlder, loadingOlder, hasMore }: Props) {
  const scroll = useRef<HTMLDivElement>(null)
  const nearBottom = useRef(true)
  const previousTop = useRef<{ height: number; top: number } | null>(null)
  const [showNew, setShowNew] = useState(false)
  const items = groupEvents(events)
  const lastId = events.at(-1)?.id

  const updatePosition = useCallback(() => {
    const element = scroll.current
    if (!element) return
    nearBottom.current = element.scrollHeight - element.scrollTop - element.clientHeight < 90
    if (nearBottom.current) setShowNew(false)
    if (element.scrollTop < 100 && events.length > 0 && hasMore && !loadingOlder && !previousTop.current) {
      previousTop.current = { height: element.scrollHeight, top: element.scrollTop }
      void loadOlder().catch(() => { previousTop.current = null })
    }
  }, [events.length, hasMore, loadingOlder, loadOlder])

  useEffect(() => {
    const element = scroll.current
    if (!element) return
    if (previousTop.current) {
      element.scrollTop = previousTop.current.top + element.scrollHeight - previousTop.current.height
      previousTop.current = null
    } else if (nearBottom.current) {
      element.scrollTop = element.scrollHeight
    } else {
      setShowNew(true)
    }
  }, [events])

  return (
    <>
      <div className="conversation-scroll" ref={scroll} onScroll={updatePosition}>
        <div className="conversation-inner">
          {hasMore && events.length > 0 && (
            <button className="button compact older-button" type="button" disabled={loadingOlder} onClick={() => {
              const element = scroll.current
              if (element) previousTop.current = { height: element.scrollHeight, top: element.scrollTop }
              void loadOlder().catch(() => { previousTop.current = null })
            }}>
              {loadingOlder ? 'Загрузка…' : 'Загрузить ранние сообщения'}
            </button>
          )}
          {items.length === 0 && <div className="empty-state conversation-empty">Скажите «Hey Jarvis» или напишите сообщение</div>}
          {items.map((item) => item.kind === 'turn'
            ? <TurnView key={item.id} events={item.events} />
            : <div className={`announcement${item.event.type === 'error' || (item.event.type === 'announcement' && item.event.kind === 'error') ? ' error' : ''}`} key={item.event.id || `error-${lastId}`}>
                <time>{item.event.ts ? new Date(item.event.ts).toLocaleTimeString('ru-RU', { hour: '2-digit', minute: '2-digit' }) : ''}</time>
                {item.event.type === 'error' ? item.event.message : item.event.type === 'announcement' ? item.event.text : ''}
              </div>)}
        </div>
        {showNew && <button className="new-messages" type="button" onClick={() => {
          scroll.current?.scrollTo({ top: scroll.current.scrollHeight, behavior: 'smooth' })
          nearBottom.current = true
          setShowNew(false)
        }}>↓ Новые сообщения</button>}
      </div>
      <Composer connected={connected} send={send} />
    </>
  )
}
