from __future__ import annotations

import logging
import tomllib
from dataclasses import dataclass, field, fields
from pathlib import Path
from typing import Any

_LOG = logging.getLogger(__name__)
_ROOT = Path(__file__).resolve().parent.parent
_SAFE_SHELL = [
    r"(?i)^Get-[A-Za-z][A-Za-z0-9]*(?:\s+.+)?$",
    r"(?i)^(ls|dir|pwd|whoami|hostname|systeminfo|date|tasklist|ipconfig)(?:\s+.+)?$",
    r"(?i)^(cat|type|where|where\.exe)\s+.+$",
    r"(?i)^git\s+(status|log|diff|show|branch)(?:\s+.+)?$",
]


@dataclass
class GeneralConfig:
    assistant_name: str = "Суббота"
    user_title: str = "сэр"
    data_dir: Path = Path("data")
    log_level: str = "INFO"


@dataclass
class AgentConfig:
    model: str = "claude-opus-5-5"
    effort: str = "medium"
    extra_instructions: str = ""


@dataclass
class VoiceConfig:
    enabled: bool = True
    wake_engine: str = "vosk"
    wake_word: str = "суббота"
    vosk_model: Path = Path("models/vosk/vosk-model-small-ru-0.22")
    wake_threshold: float = 0.5
    whisper_model: str = "large-v3-turbo"
    whisper_device: str = "cuda"
    whisper_compute_type: str = "float16"
    language: str = "ru"
    tts_engine: str = "silero"
    silero_model: Path = Path("models/silero/v5_1_ru.pt")
    silero_speaker: str = "xenia"
    piper_voice: Path = Path("models/piper/ru_RU-denis-medium.onnx")
    input_device: int | str | None = None
    output_device: int | str | None = None
    follow_up_s: float = 6.0
    silence_s: float = 0.9
    max_record_s: float = 15.0


@dataclass
class ServerConfig:
    host: str = "127.0.0.1"
    port: int = 8765
    open_browser_on_start: bool = False


@dataclass
class PermissionsConfig:
    auto_allow: list[str] = field(default_factory=list)
    always_ask: list[str] = field(default_factory=list)
    safe_shell: list[str] = field(default_factory=lambda: list(_SAFE_SHELL))
    # balanced — команды без признаков опасности выполняются сами (subbota/shell_risk.py);
    # strict — без спроса только команды из safe_shell.
    shell_policy: str = "balanced"
    approval_timeout_s: int = 90


@dataclass
class BrowserConfig:
    enabled: bool = True
    command: str = "npx"
    args: list[str] = field(default_factory=lambda: ["-y", "@playwright/mcp@latest"])


@dataclass
class CodeConfig:
    workspace_roots: list[Path] = field(default_factory=lambda: [Path("~/Desktop")])
    max_parallel: int = 2
    max_budget_usd: float = 5.0
    effort: str = "high"


@dataclass
class Config:
    general: GeneralConfig = field(default_factory=GeneralConfig)
    agent: AgentConfig = field(default_factory=AgentConfig)
    voice: VoiceConfig = field(default_factory=VoiceConfig)
    server: ServerConfig = field(default_factory=ServerConfig)
    permissions: PermissionsConfig = field(default_factory=PermissionsConfig)
    browser: BrowserConfig = field(default_factory=BrowserConfig)
    code: CodeConfig = field(default_factory=CodeConfig)
    apps: dict[str, str] = field(default_factory=dict)
    _root: Path = field(default=_ROOT, repr=False)

    @property
    def data_dir(self) -> Path:
        path = _resolve_path(self._root, self.general.data_dir)
        path.mkdir(parents=True, exist_ok=True)
        return path

    @property
    def workspace_dir(self) -> Path:
        path = self.data_dir / "workspace"
        path.mkdir(parents=True, exist_ok=True)
        return path


def _resolve_path(root: Path, value: str | Path) -> Path:
    path = Path(value).expanduser()
    return (path if path.is_absolute() else root / path).resolve()


def _section(name: str, cls: type, raw: Any) -> Any:
    if not isinstance(raw, dict):
        _LOG.warning("Секция %s должна быть таблицей", name)
        return cls()
    allowed = {item.name for item in fields(cls)}
    for key in raw.keys() - allowed:
        _LOG.warning("Неизвестный ключ конфигурации: %s.%s", name, key)
    return cls(**{key: value for key, value in raw.items() if key in allowed})


def load_config(path: Path | None = None) -> Config:
    root = _ROOT
    source = _resolve_path(root, path) if path is not None else root / "config.toml"
    try:
        with source.open("rb") as stream:
            raw = tomllib.load(stream)
    except FileNotFoundError:
        raw = {}
    cfg = Config(_root=root)
    for name, cls in (
        ("general", GeneralConfig), ("agent", AgentConfig), ("voice", VoiceConfig),
        ("server", ServerConfig), ("permissions", PermissionsConfig),
        ("browser", BrowserConfig), ("code", CodeConfig),
    ):
        setattr(cfg, name, _section(name, cls, raw.get(name, {})))
    for name in raw.keys() - {item.name for item in fields(Config) if not item.name.startswith("_")}:
        _LOG.warning("Неизвестная секция конфигурации: %s", name)
    cfg.apps = raw.get("apps", {})
    cfg.general.data_dir = _resolve_path(root, cfg.general.data_dir)
    cfg.voice.piper_voice = _resolve_path(root, cfg.voice.piper_voice)
    cfg.voice.silero_model = _resolve_path(root, cfg.voice.silero_model)
    cfg.voice.vosk_model = _resolve_path(root, cfg.voice.vosk_model)
    cfg.code.workspace_roots = [_resolve_path(root, item) for item in cfg.code.workspace_roots]
    return cfg
