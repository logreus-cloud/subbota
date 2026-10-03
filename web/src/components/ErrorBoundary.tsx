import { Component, type ReactNode } from 'react'

type State = { error: Error | null }

// Ошибка рендера одного события не должна превращать всю панель в белый экран.
export class ErrorBoundary extends Component<{ children: ReactNode }, State> {
  state: State = { error: null }

  static getDerivedStateFromError(error: Error): State {
    return { error }
  }

  render() {
    if (!this.state.error) return this.props.children
    return (
      <div className="fatal">
        <h1>Панель упала</h1>
        <p>{this.state.error.message}</p>
        <button className="button" type="button" onClick={() => location.reload()}>Перезагрузить</button>
      </div>
    )
  }
}
