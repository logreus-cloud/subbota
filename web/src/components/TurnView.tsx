import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import type { Event } from '../types'
import { ToolCard } from './ToolCard'

export function Markdown({ text }: { text: string }) {
  return (
    <div className="markdown">
      {/* Картинки не грузим: текст может прийти из веба и утечь данные через URL. */}
      <ReactMarkdown remarkPlugins={[remarkGfm]} disallowedElements={['img']} unwrapDisallowed components={{
        a: ({ node: _node, ...props }) => <a {...props} target="_blank" rel="noreferrer" />,
      }}>{text}</ReactMarkdown>
    </div>
  )
}

const sourceIcons: Record<string, string> = {
  voice: '🎙',
  text: '⌨',
  scheduler: '◷',
  system: '◇',
}

export function TurnView({ events }: { events: Event[] }) {
  const user = events.find((event) => event.type === 'user_message')
  const done = events.findLast((event) => event.type === 'turn_done')
  const results = new Map<string, Extract<Event, { type: 'tool_result' }>>()
  for (const event of events) {
    if (event.type === 'tool_result') results.set(event.tool_use_id, event)
  }
  const blocks = events.filter((event) => event.type === 'assistant_message' || event.type === 'tool_call')
  const stamp = user?.ts ?? events[0]?.ts

  return (
    <article className="turn">
      {user?.type === 'user_message' && (
        <div className="user-message">
          <div className="user-bubble">{user.text}</div>
          <span className="source-icon" title={user.source} aria-label={user.source === 'voice' ? 'Голос' : user.source === 'text' ? 'Клавиатура' : 'Планировщик'}>{sourceIcons[user.source]}</span>
        </div>
      )}
      {blocks.map((event) => event.type === 'assistant_message'
        ? <div className="assistant-block" key={event.id}><Markdown text={event.text} /></div>
        : event.type === 'tool_call'
          ? <ToolCard key={event.tool_use_id} call={event} result={results.get(event.tool_use_id)} />
          : null)}
      {done?.type === 'turn_done' && done.is_error && blocks.length === 0 && <div className="announcement error">{done.result || 'Ошибка при выполнении запроса'}</div>}
      <div className="turn-meta">
        {stamp && new Date(stamp).toLocaleString('ru-RU', { hour: '2-digit', minute: '2-digit', day: '2-digit', month: 'short' })}
        {done?.type === 'turn_done' && typeof done.cost_usd === 'number' && <> · ${done.cost_usd.toFixed(4)}</>}
      </div>
    </article>
  )
}
