# Jarvis — архитектура

Голосовой ассистент для Windows 11, постоянно работающий в фоне. Мозг — Claude Opus 5.5 через Claude Agent SDK (Python). Голос обрабатывается локально. Управление — веб-панель на localhost и иконка в трее.

## Стек

- Python 3.12, менеджер `uv`, пакет `jarvis/` (запуск `uv run python -m jarvis`).
- Мозг: `claude-agent-sdk` 0.2.x (`ClaudeSDKClient`, `ClaudeAgentOptions`, `tool`, `create_sdk_mcp_server`, `can_use_tool`). Авторизация берётся из входа в Claude Code, ключ API не нужен.
- Голос: `openwakeword` 0.4.0 (модель `hey_jarvis` уже в пакете: `openwakeword.models["hey_jarvis"]["model_path"]`, конструктор `Model(wakeword_model_paths=[...])`), `faster-whisper` на CUDA (перед импортом нужно добавить в DLL-пути папки `site-packages/nvidia/*/bin`), `piper-tts` (голос `models/piper/ru_RU-denis-medium.onnx`, `PiperVoice.load(path)`), `sounddevice`.
- Сервер: FastAPI + uvicorn, WebSocket. Только `127.0.0.1`.
- Планировщик: APScheduler 3 (`AsyncIOScheduler`), напоминания хранятся в SQLite.
- Трей: `pystray` + `Pillow`.
- Система: `pycaw` (громкость), `pywin32` (окна, буфер обмена, клавиши), `psutil`.
- Браузер: MCP-сервер Playwright (`npx @playwright/mcp@latest`, stdio).
- Frontend: `web/` на Vite + React + TypeScript, сборка в `web/dist`, её раздаёт FastAPI.

## Структура

```
jarvis/
  __main__.py      точка входа: собирает AppContext, запускает всё, корректно гасит
  config.py        загрузка config.toml (tomllib) в dataclass Config
  cuda.py          add_nvidia_dll_dirs(): вызвать до импорта faster_whisper
  context.py       AppContext — общий контейнер сервисов
  db.py            SQLite: events, reminders, kv
  events.py        EventBus: pub/sub + запись в db.events
  permissions.py   PermissionGate: решает, можно ли вызывать инструмент; очередь подтверждений
  agent.py         Brain: одна постоянная сессия ClaudeSDKClient, очередь запросов
  prompts.py       системный промпт Джарвиса
  scheduler.py     ReminderScheduler
  server.py        FastAPI-приложение: REST + /ws + статика web/dist
  tray.py          иконка в трее
  tools/
    __init__.py    build_jarvis_server(ctx) -> McpSdkServerConfig; список имён инструментов
    system.py      громкость, медиа, приложения, окна, буфер, скриншот, питание, info
    reminders.py   add/list/cancel напоминаний
    memory.py      remember/forget/recall (data/memory.md)
    code.py        фоновые задачи по коду в проектах (под-агент Agent SDK)
  voice/
    audio.py       MicStream (16 кГц mono int16, блоки по 1280 сэмплов)
    wake.py        WakeWordDetector
    stt.py         Transcriber (faster-whisper)
    tts.py         Speaker (Piper + sounddevice, прерываемый)
    loop.py        VoiceLoop: конечный автомат в отдельном потоке
web/               frontend
scripts/install-autostart.ps1, scripts/uninstall-autostart.ps1
config.example.toml  (config.toml в .gitignore; если его нет, берутся значения по умолчанию)
data/              (в .gitignore) jarvis.db, memory.md, jarvis.log
models/            (в .gitignore) piper-голоса
```

## AppContext (`context.py`)

```python
@dataclass
class AppContext:
    cfg: Config
    bus: EventBus
    db: Database
    gate: PermissionGate
    loop: asyncio.AbstractEventLoop          # главный event loop
    brain: "Brain | None" = None
    scheduler: "ReminderScheduler | None" = None
    voice: "VoiceLoop | None" = None
    code_tasks: "CodeTaskManager | None" = None
    state: dict                               # runtime: {"status": "idle", "mic_muted": False}

    async def announce(self, text: str, kind: str = "info") -> None
        # публикует событие "announcement" и, если голос включён и не выключен звук, озвучивает text
    def set_status(self, status: str) -> None   # потокобезопасно; публикует "status"
```

Потоки: всё асинхронное живёт в главном loop. Голос и трей работают в своих потоках и обращаются к loop только через `asyncio.run_coroutine_threadsafe(..., ctx.loop)` или `loop.call_soon_threadsafe`.

## События (EventBus → WebSocket)

Каждое событие — JSON `{"id": int, "ts": iso8601, "type": str, ...payload}`. `id` выдаёт таблица `events` (автоинкремент). Не сохраняются в БД только `status` и `assistant_delta`, им назначается `id = 0`.

| type | payload |
|---|---|
| status | `status`: idle / listening / recording / transcribing / thinking / speaking; `mic_muted`: bool |
| user_message | `turn_id`, `text`, `source`: voice / text / scheduler / system |
| assistant_message | `turn_id`, `text` (текстовый блок ответа, markdown) |
| tool_call | `turn_id`, `tool_use_id`, `name`, `input` (dict, длинные строки обрезаны до 2000 символов) |
| tool_result | `turn_id`, `tool_use_id`, `is_error`, `text` (обрезан до 4000) |
| turn_done | `turn_id`, `result` (итоговый текст), `is_error`, `cost_usd`, `duration_ms` |
| approval_request | `approval_id`, `tool`, `title`, `detail` (строка: команда, путь или JSON входа), `source`: main / code:<task_id> |
| approval_resolved | `approval_id`, `allowed`, `by`: ui / voice / timeout / session_rule |
| announcement | `text`, `kind`: info / reminder / code_task / error |
| reminders_changed | — |
| code_task | `task_id`, `project`, `task`, `status`: running / done / failed / cancelled, `summary` |
| session_reset | — |
| error | `message` |

## WebSocket `/ws`

После подключения сервер шлёт `{"type": "snapshot", "state": ctx.state, "history": [последние 300 событий из БД по возрастанию id], "approvals": [...ожидающие], "reminders": [...], "code_tasks": [...]}`, а затем поток событий.

Сообщения от клиента:
- `{"type": "chat", "text": str}` — запрос в Brain с `source="text"`;
- `{"type": "approve", "approval_id": str, "allow": bool, "remember": bool}`; `remember=true` разрешает этот инструмент до конца сессии;
- `{"type": "interrupt"}` — прервать текущий ход и замолчать;
- `{"type": "reset"}` — новый разговор;
- `{"type": "mute", "muted": bool}` — выключить или включить микрофон;
- `{"type": "speak", "text": str}` — озвучить текст (для проверки голоса).

## REST

- `GET /api/history?before=<id>&limit=200`
- `GET /api/reminders`, `POST /api/reminders` `{text, when, recurrence?, kind?}`, `DELETE /api/reminders/{id}`
- `GET /api/memory` → `{text}`, `PUT /api/memory` `{text}`
- `GET /api/projects` → `[{name, path}]`
- `GET /api/code-tasks`, `POST /api/code-tasks/{id}/cancel`
- `GET /api/config` — конфиг без секретов

Безопасность: сервер слушает только `127.0.0.1`. Для `/ws` и всех не-GET запросов проверяется заголовок `Origin`: разрешены только `http://127.0.0.1:<port>`, `http://localhost:<port>` и dev-адреса `http://localhost:5173`, `http://127.0.0.1:5173`. Отсутствующий Origin разрешён только у не-браузерных REST-клиентов, у WebSocket — нет. Иначе любой сайт в браузере мог бы управлять PowerShell.

## Права (PermissionGate)

`async def check(tool_name, tool_input, ctx, scope) -> PermissionResultAllow | PermissionResultDeny`, где `scope` — `"main"` или `CodeScope(task_id, project_dir)`.

Порядок проверки:
1. Правила сессии (`remember=true`) по имени инструмента → allow.
2. `cfg.permissions.always_ask` (fnmatch по имени) → ask.
3. Автоматически разрешено: `Read`, `Glob`, `Grep`, `WebSearch`, `WebFetch`, `TodoWrite`, безопасные `mcp__jarvis__*` (всё, кроме `power_action`, `close_window`, `set_clipboard`), `mcp__playwright__browser_*`, кроме `browser_file_upload`, `browser_evaluate`, `browser_run_code`, `browser_install`. К этому добавляется `cfg.permissions.auto_allow`.
4. `Bash` / `PowerShell`: allow, если команда целиком совпадает с одной из регулярок `cfg.permissions.safe_shell` (по умолчанию: только чтение — `Get-*`, `ls`, `dir`, `cat`, `type`, `echo`, `git status|log|diff|show|branch`, `where`, `whoami`, `hostname`, `ipconfig`, `systeminfo`, `tasklist`) и в ней нет `;`, `|`, `&`, `>`, `` ` ``, `$(`. Иначе ask.
5. `Write` / `Edit` / `NotebookEdit`: в `CodeScope` allow, если путь внутри `project_dir`. В main allow, если путь внутри `data_dir/workspace`. Иначе ask.
6. Всё остальное → ask.

ask: создаётся `Approval(id, tool, title, detail, source, future)`, публикуется `approval_request`, затем `ctx.voice.confirm(question)`, если голос активен (ответ голосом «да» или «нет» разрешает запрос, `None` игнорируется). Ждём первый ответ (UI, голос) или таймаут `cfg.permissions.approval_timeout_s` (по умолчанию 180 с). Отказ возвращается как `PermissionResultDeny(message="Пользователь отказал в действии")`. Ожидающие подтверждения доступны через `gate.pending()`.

## Brain (`agent.py`)

- Одна постоянная сессия `ClaudeSDKClient`. Подключение, `query` и `receive_response` выполняются в одной worker-задаче: SDK требует, чтобы клиент использовался в той задаче, где он создан.
- Опции: `model=cfg.agent.model` (claude-opus-5-5), `effort=cfg.agent.effort` (по умолчанию "medium"), `system_prompt=` строка из `prompts.build_system_prompt(ctx)` (не пресет Claude Code: он дорогой), `tools=["Bash","PowerShell","Read","Write","Edit","Glob","Grep","WebSearch","WebFetch","TodoWrite"]`, `mcp_servers={"jarvis": build_jarvis_server(ctx), "playwright": {...} если cfg.browser.enabled}`, `can_use_tool=gate.check(..., scope="main")`, `permission_mode="default"`, `setting_sources=[]` (не подтягивать пользовательские CLAUDE.md, хуки и плагины), `cwd=data_dir/workspace`, `resume=` сохранённый session_id из `kv`, если есть. `allowed_tools` не задаём, чтобы все вызовы шли через `can_use_tool`.
- Если resume не удался (ошибка при connect), подключаемся заново без resume и стираем ключ.
- `async def ask(text, source) -> TurnResult(turn_id, text, is_error)` ставит запрос в очередь и ждёт результат. `async def submit(text, source) -> str turn_id` работает без ожидания.
- В модель уходит текст с префиксом `[{source_label} · {YYYY-MM-DD HH:MM, день недели}] `. source_label: «голос», «панель», «планировщик», «система».
- Пока идёт ход: AssistantMessage → TextBlock публикуется как `assistant_message`, ToolUseBlock как `tool_call`. UserMessage с ToolResultBlock → `tool_result`. ResultMessage → `turn_done` и сохранение `session_id` в `kv`. Статус `thinking` на время хода, затем `idle` (голос сам ставит `speaking`).
- `interrupt()` вызывает `client.interrupt()` (можно из другой задачи). `reset()` через сентинел в очереди: disconnect, удалить session_id, connect, событие `session_reset`.
- Ошибки сети и SDK: событие `error`, ход возвращается с `is_error=True`, worker переподключается с экспоненциальной задержкой (до 60 с) и не падает.

## Голос (`voice/loop.py`)

Конечный автомат в отдельном потоке: IDLE (ждёт wake word) → RECORDING (короткий сигнал «дзинь», запись до 0,9 с тишины после речи, максимум 15 с, без речи 5 с — обратно в IDLE) → TRANSCRIBING → отправка в `brain.ask(text, "voice")` → SPEAKING ответа → FOLLOW_UP (слушает `cfg.voice.follow_up_s` секунд без wake word) → IDLE.

- Стоп-фразы («стоп», «хватит», «замолчи», «отмена») в начале фразы: `brain.interrupt()` и `speaker.stop()`.
- Wake word во время SPEAKING прерывает речь (barge-in).
- `say(text)` — потокобезопасная очередь на озвучку; перед синтезом из текста убирается markdown, ссылки и блоки кода.
- `confirm(question) -> concurrent.futures.Future[bool | None]`: озвучить вопрос, записать ответ, разобрать «да / разрешаю / давай / ок / выполняй» и «нет / отмена / не надо / запрещаю».
- `set_muted(bool)`: при выключенном микрофоне wake word не слушается, но озвучка работает.

## Код (`tools/code.py`)

`CodeTaskManager.start(project, task) -> task_id` запускает фоновую `asyncio`-задачу `query()` с `cwd=project_dir`, `system_prompt={"type":"preset","preset":"claude_code"}`, `setting_sources=["project"]`, `effort="high"`, `can_use_tool=gate.check(..., scope=CodeScope(task_id, project_dir))` и `max_budget_usd=cfg.code.max_budget_usd`. Проект ищется среди подпапок `cfg.code.workspace_roots` по имени (без учёта регистра) или по абсолютному пути внутри этих корней; остальное запрещено. По завершении: событие `code_task`, затем `ctx.announce(краткий итог)`. Одновременно идёт не больше `cfg.code.max_parallel` задач.
