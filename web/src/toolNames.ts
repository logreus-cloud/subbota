const names: Record<string, string> = {
  Bash: 'Команда',
  PowerShell: 'Команда',
  Read: 'Чтение файла',
  Write: 'Запись файла',
  Edit: 'Изменение файла',
  Glob: 'Поиск файлов',
  Grep: 'Поиск в файлах',
  WebSearch: 'Поиск в интернете',
  WebFetch: 'Открытие страницы',
  TodoWrite: 'План',
  mcp__jarvis__set_volume: 'Громкость',
  mcp__jarvis__get_volume: 'Громкость',
  mcp__jarvis__power_action: 'Питание',
  mcp__jarvis__close_window: 'Закрытие окна',
  mcp__jarvis__set_clipboard: 'Буфер обмена',
  mcp__jarvis__add_reminder: 'Напоминание',
  mcp__jarvis__cancel_reminder: 'Отмена напоминания',
  mcp__jarvis__start_code_task: 'Задача кода',
  mcp__playwright__browser_navigate: 'Браузер: переход',
  mcp__playwright__browser_click: 'Браузер: нажатие',
  mcp__playwright__browser_snapshot: 'Браузер: снимок',
  mcp__playwright__browser_type: 'Браузер: ввод',
}

export function toolName(name: string) {
  return names[name] ?? name.replace(/^mcp__[^_]+__/, '').replaceAll('_', ' ')
}

export function toolSummary(input: Record<string, unknown>) {
  const priority = ['command', 'url', 'file_path', 'path', 'text', 'query', 'value', 'project', 'task']
  const key = priority.find((item) => item in input) ?? Object.keys(input)[0]
  if (!key) return 'Без параметров'
  const value = input[key]
  const rendered = typeof value === 'string' ? value : JSON.stringify(value)
  return rendered.length > 140 ? `${rendered.slice(0, 137)}…` : rendered
}
