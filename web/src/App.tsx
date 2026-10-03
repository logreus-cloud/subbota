import { useState } from 'react'
import { Core, CoreActions } from './components/Core'
import { Conversation } from './components/Conversation'
import { SidePanel } from './components/SidePanel'
import { useSubbota } from './useSubbota'

function App() {
  const { state, send, loadOlder, loadingOlder, hasMore } = useSubbota()
  const [panelOpen, setPanelOpen] = useState(false)

  return (
    <div className={`app status-${state.status}${state.mic_muted ? ' muted' : ''}`}>
      <Core
        status={state.status}
        muted={state.mic_muted}
        connected={state.connected}
        events={state.events}
        send={send}
      />
      <main className="conversation-column">
        <header className="conversation-header">
          <div className="mobile-identity">
            <span className="mini-core" aria-hidden="true" />
            <span>СУББОТА <small>{state.mic_muted ? 'Микрофон выключен' : state.status === 'idle' ? 'Скажите «Суббота»' : state.status === 'thinking' ? 'Думаю' : state.status === 'speaking' ? 'Говорю' : state.status === 'transcribing' ? 'Распознаю' : 'Слушаю'}</small></span>
          </div>
          <h1>Разговор</h1>
          <button className="panel-toggle" type="button" onClick={() => setPanelOpen(true)}>
            Панель{state.approvals.length > 0 && <span className="badge">{state.approvals.length}</span>}
          </button>
        </header>
        <Conversation
          events={state.events}
          connected={state.connected}
          send={send}
          loadOlder={loadOlder}
          loadingOlder={loadingOlder}
          hasMore={hasMore}
        />
      </main>
      {panelOpen && <button className="panel-backdrop" type="button" aria-label="Закрыть панель" onClick={() => setPanelOpen(false)} />}
      <SidePanel
        open={panelOpen}
        onClose={() => setPanelOpen(false)}
        approvals={state.approvals}
        reminders={state.reminders}
        codeTasks={state.code_tasks}
        send={send}
        connected={state.connected}
        controls={<CoreActions status={state.status} muted={state.mic_muted} connected={state.connected} send={send} />}
      />
    </div>
  )
}

export default App
