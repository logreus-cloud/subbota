# Суббота

Локальный голосовой ассистент для Windows 11. Работает в фоне, отвечает через Claude Code, показывает события и запросы подтверждения в веб-панели.

## Требования

Windows 11, Python 3.12, `uv`, Node.js и вход в Claude Code: запустите `claude` в терминале и авторизуйтесь. Для голосового режима нужны микрофон, динамики и подходящая видеокарта NVIDIA.

## Установка

```powershell
uv sync
# Голос Silero (по умолчанию) и модель Vosk для обращения «Суббота»
New-Item -ItemType Directory -Force models/silero, models/vosk | Out-Null
Invoke-WebRequest https://models.silero.ai/models/tts/ru/v5_1_ru.pt -OutFile models/silero/v5_1_ru.pt
Invoke-WebRequest https://alphacephei.com/vosk/models/vosk-model-small-ru-0.22.zip -OutFile models/vosk/small-ru.zip
Expand-Archive models/vosk/small-ru.zip models/vosk; Remove-Item models/vosk/small-ru.zip
# Запасной голос Piper (tts_engine = "piper")
uv run python -m piper.download_voices --data-dir models/piper ru_RU-denis-medium
cd web
npm install
npm run build
```

Вернитесь в корень проекта и запустите `uv run python -m subbota`. Панель доступна по адресу `http://127.0.0.1:8765/`. Для запуска без голоса или трея используйте `--no-voice` и `--no-tray`.

## Голос и управление

Скажите «Суббота», затем произнесите запрос. «Стоп», «хватит», «замолчи» и «отмена» прерывают ответ. После ответа несколько секунд можно задать следующий вопрос без ключевой фразы.

Действия, требующие прав, появляются в панели как запросы подтверждения. Их можно разрешить или отклонить; разрешение с запоминанием действует до конца сессии.

## Настройки и автозапуск

Скопируйте `config.example.toml` в `config.toml` и измените нужные параметры. Без `config.toml` действуют значения по умолчанию.

После `uv sync` запустите `scripts/install-autostart.ps1` в PowerShell для автозапуска при входе в Windows. Удаление задачи: `scripts/uninstall-autostart.ps1`.

Логи находятся в `data/subbota.log`.

