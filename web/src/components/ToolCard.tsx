import type { Event } from '../types'
import { toolName, toolSummary } from '../toolNames'

type Call = Extract<Event, { type: 'tool_call' }>
type Result = Extract<Event, { type: 'tool_result' }>

export function ToolCard({ call, result }: { call: Call; result?: Result }) {
  return (
    <details className="tool-card">
      <summary>
        <span className="tool-title">{toolName(call.name)}</span>
        <span className="tool-summary">{toolSummary(call.input)}</span>
        <span className={`tool-state${result?.is_error ? ' error' : ''}`} aria-label={!result ? 'Выполняется' : result.is_error ? 'Ошибка' : 'Готово'}>
          {!result ? <span className="spinner" /> : result.is_error ? '✕' : '✓'}
        </span>
      </summary>
      <div className="tool-detail">
        <small>Вход</small>
        <pre className="detail-block">{JSON.stringify(call.input, null, 2)}</pre>
        {result && <>
          <small>Результат</small>
          <pre className="detail-block">{result.text}</pre>
        </>}
      </div>
    </details>
  )
}
