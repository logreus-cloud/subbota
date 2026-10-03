from __future__ import annotations

import asyncio
import fnmatch
import json
import logging
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any
from uuid import uuid4

from claude_agent_sdk import PermissionResultAllow, PermissionResultDeny, ToolPermissionContext
from claude_agent_sdk.types import HookCallback, HookContext, HookInput, HookJSONOutput

if TYPE_CHECKING:
    from subbota.context import AppContext

_LOG = logging.getLogger(__name__)
_SAFE_TOOLS = {"Read", "Glob", "Grep", "WebSearch", "WebFetch", "TodoWrite"}
_UNSAFE_SHELL_CHARS = set(";|&><`$(){}[]@%,\'\"\r\n")
_UNSAFE_GET_OPTIONS = ("-outfile", "-outvariable", "-pipelinevariable")
_UNSAFE_GIT_OPTIONS = (
    "-o", "--output", "--ext-diff", "--exec", "-c",
    "--git-dir", "--work-tree", "--textconv",
)
_VOICE_DETAIL_LIMIT = 140
_UNSAFE_СУББОТА = {"power_action", "close_window", "set_clipboard", "cancel_code_task"}
_UNSAFE_BROWSER = {"browser_file_upload", "browser_evaluate", "browser_run_code", "browser_install"}


def is_safe_shell(command: str, patterns: list[str]) -> bool:
    if any(char in _UNSAFE_SHELL_CHARS for char in command):
        return False
    tokens = command.split()
    if not tokens:
        return False
    name, args = tokens[0].casefold(), tokens[1:]
    if name.startswith("get-"):
        safe = re.fullmatch(r"get-[a-z][a-z0-9]*", name) is not None and not any(
            token.casefold().startswith(_UNSAFE_GET_OPTIONS) for token in args
        )
    elif name in {"ls", "dir", "cat", "type"}:
        safe = (name in {"ls", "dir"} or bool(args)) and all(
            not token.startswith(("-", "/")) for token in args
        )
    elif name in {"pwd", "whoami", "hostname", "systeminfo", "date", "tasklist"}:
        safe = not args
    elif name == "ipconfig":
        safe = not args or len(args) == 1 and args[0].casefold() == "/all"
    elif name in {"where", "where.exe"}:
        safe = len(args) == 1 and not args[0].startswith(("-", "/"))
    elif name == "git" and len(args) >= 1:
        subcommand, *options = args
        if subcommand.casefold() == "branch":
            safe = all(option in {"-a", "-r", "--list", "-v", "-vv"} for option in options)
        else:
            safe = subcommand.casefold() in {"status", "log", "diff", "show"} and not any(
                token.casefold().startswith(_UNSAFE_GIT_OPTIONS) for token in options
            )
    else:
        safe = False
    if not safe:
        return False
    return any(re.fullmatch(pattern, command) is not None for pattern in patterns)


def resolve_tool_path(raw: str, cwd: Path) -> Path:
    path = Path(raw).expanduser()
    root = Path(cwd).expanduser().resolve()
    return (path if path.is_absolute() else root / path).resolve()


@dataclass
class CodeScope:
    task_id: str
    project_dir: Path


@dataclass
class Approval:
    id: str
    tool: str
    title: str
    detail: str
    source: str
    created_at: str
    future: asyncio.Future[bool]

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "tool": self.tool,
            "title": self.title,
            "detail": self.detail,
            "source": self.source,
            "created_at": self.created_at,
        }


class PermissionGate:
    def __init__(self, ctx: AppContext) -> None:
        self.ctx = ctx
        self._pending: dict[str, Approval] = {}
        self._session_rules: set[str] = set()
        self.workspace_root: Path | None = None
        self._workspace_dir = asyncio.create_task(
            asyncio.to_thread(lambda: ctx.cfg.workspace_dir.resolve())
        )

    async def ready(self) -> Path:
        self.workspace_root = await asyncio.shield(self._workspace_dir)
        return self.workspace_root

    async def check(
        self,
        tool_name: str,
        tool_input: dict[str, Any],
        context: ToolPermissionContext | None = None,
        scope: str | CodeScope = "main",
    ) -> PermissionResultAllow | PermissionResultDeny:
        cfg = self.ctx.cfg.permissions
        path = None
        if tool_name in {"Write", "Edit", "NotebookEdit"}:
            key = "notebook_path" if tool_name == "NotebookEdit" else "file_path"
            value = tool_input.get(key)
            if isinstance(value, str) and value:
                root = (
                    await asyncio.to_thread(scope.project_dir.resolve)
                    if isinstance(scope, CodeScope) else await self.ready()
                )
                path = await asyncio.to_thread(resolve_tool_path, value, root)
                tool_input = {**tool_input, key: str(path)}
        if any(fnmatch.fnmatchcase(tool_name, pattern) for pattern in cfg.always_ask):
            return await self._ask(tool_name, tool_input, context, scope)
        if tool_name in self._session_rules:
            return self._allow(tool_name, tool_input, "правило сессии")
        if (
            tool_name in _SAFE_TOOLS
            or tool_name.startswith("mcp__subbota__")
            and tool_name.removeprefix("mcp__subbota__") not in _UNSAFE_СУББОТА
            or tool_name.startswith("mcp__playwright__browser_")
            and tool_name.removeprefix("mcp__playwright__") not in _UNSAFE_BROWSER
            or any(fnmatch.fnmatchcase(tool_name, pattern) for pattern in cfg.auto_allow)
        ):
            return self._allow(tool_name, tool_input, "автоматически")
        if tool_name in {"Bash", "PowerShell"}:
            command = str(tool_input.get("command", ""))
            # В Bash обратный слеш экранирует (\--output), в PowerShell это путь.
            bash_escape = tool_name == "Bash" and "\\" in command
            if not bash_escape and is_safe_shell(command, cfg.safe_shell):
                return self._allow(tool_name, tool_input, "безопасная команда")
        if tool_name in {"Write", "Edit", "NotebookEdit"}:
            if path is not None and path.is_relative_to(root):
                return self._allow(tool_name, tool_input, "рабочая папка")
        return await self._ask(tool_name, tool_input, context, scope)

    def _allow(self, tool: str, tool_input: dict, reason: str) -> PermissionResultAllow:
        _LOG.info("Разрешено %s: %s", tool, reason)
        return PermissionResultAllow(updated_input=tool_input)

    async def _ask(
        self,
        tool: str,
        tool_input: dict[str, Any],
        context: ToolPermissionContext | None,
        scope: str | CodeScope,
    ) -> PermissionResultAllow | PermissionResultDeny:
        path = tool_input.get("file_path") or tool_input.get("notebook_path")
        if tool in {"Bash", "PowerShell"}:
            title = "Выполнить команду PowerShell" if tool == "PowerShell" else "Выполнить команду"
            detail = str(tool_input.get("command", ""))
        elif tool in {"Write", "Edit", "NotebookEdit"}:
            title = f"Записать файл {path}"
            detail = str(path)
        else:
            title = f"Вызвать инструмент {tool}"
            detail = json.dumps(tool_input, ensure_ascii=False, default=str)
        title = context.title or title if context is not None else title
        approval = Approval(
            id=uuid4().hex,
            tool=tool,
            title=title,
            detail=detail[:1500],
            source=f"code:{scope.task_id}" if isinstance(scope, CodeScope) else "main",
            created_at=datetime.now().astimezone().isoformat(timespec="seconds"),
            future=asyncio.get_running_loop().create_future(),
        )
        self._pending[approval.id] = approval
        _LOG.info("Запрошено подтверждение %s: %s", tool, approval.title)
        self.ctx.bus.publish(
            "approval_request", approval_id=approval.id, tool=tool, title=title,
            detail=approval.detail, source=approval.source,
        )
        voice_task = None
        if self.ctx.voice is not None and not self.ctx.state["mic_muted"]:
            # Голосом подтверждают только то, что можно целиком произнести:
            # иначе «да» уходит на команду, которую пользователь не слышал.
            user_title = self.ctx.cfg.general.user_title
            spoken = " ".join(detail.split())
            if spoken and spoken in title:
                spoken = ""
            # Озвучка чистит текст (ссылки → «ссылка», убирает `*`, `>`, `_`):
            # если чистка что-то меняет, пользователь услышит не то, что выполнится.
            from subbota.voice.tts import clean_for_speech
            verbatim = all(
                " ".join(clean_for_speech(part).split()) == " ".join(part.split())
                for part in (title, spoken)
            )
            # У записи файлов содержимое не озвучивается вовсе — только панель.
            writes_file = tool in {"Write", "Edit", "NotebookEdit"}
            if verbatim and not writes_file and len(spoken) <= _VOICE_DETAIL_LIMIT:
                tail = f". {spoken}?" if spoken else "?"
                question = f"{user_title}, разрешите: {title}{tail}"
                try:
                    voice_task = asyncio.create_task(self._voice_answer(approval.id, question))
                except Exception:
                    _LOG.exception("Не удалось запросить голосовое подтверждение")
            else:
                self.ctx.voice.say(f"{user_title}, нужно подтверждение в панели: {title}.")
        try:
            allowed = await asyncio.wait_for(
                asyncio.shield(approval.future), cfg_timeout(self.ctx)
            )
        except TimeoutError:
            if self.resolve(approval.id, False, by="timeout"):
                allowed = False
            else:
                allowed = approval.future.result()
        finally:
            if voice_task is not None:
                voice_task.cancel()
            self._pending.pop(approval.id, None)
        if allowed:
            return PermissionResultAllow(updated_input=tool_input)
        return PermissionResultDeny(message="Пользователь отказал в действии")

    async def _voice_answer(self, approval_id: str, question: str) -> None:
        try:
            answer = await asyncio.wrap_future(self.ctx.voice.confirm(question))
            if isinstance(answer, bool):
                self.resolve(approval_id, answer, by="voice")
        except asyncio.CancelledError:
            raise
        except Exception:
            _LOG.exception("Ошибка голосового подтверждения")

    def resolve(self, approval_id: str, allow: bool, remember: bool = False, by: str = "ui") -> bool:
        approval = self._pending.get(approval_id)
        if approval is None or approval.future.done():
            return False
        if allow and remember:
            self._session_rules.add(approval.tool)
        approval.future.set_result(allow)
        self._pending.pop(approval_id, None)
        _LOG.info("%s %s: %s", "Разрешено" if allow else "Отказано", approval.tool, by)
        self.ctx.bus.publish("approval_resolved", approval_id=approval_id, allowed=allow, by=by)
        return True

    def pending(self) -> list[dict]:
        return [approval.to_dict() for approval in self._pending.values()]

    def clear_session_rules(self) -> None:
        self._session_rules.clear()


def make_pre_tool_hook(gate: PermissionGate, scope: str | CodeScope) -> HookCallback:
    async def hook(
        data: HookInput, _tool_use_id: str | None, _context: HookContext
    ) -> HookJSONOutput:
        try:
            if data["hook_event_name"] != "PreToolUse":
                raise ValueError("Неожиданное событие хука")
            result = await gate.check(data["tool_name"], data["tool_input"], scope=scope)
            if isinstance(result, PermissionResultAllow):
                return {"hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "permissionDecision": "allow",
                    "updatedInput": result.updated_input or data["tool_input"],
                }}
            reason = result.message
        except Exception:
            _LOG.exception("Ошибка проверки прав в PreToolUse")
            reason = "Ошибка проверки прав"
        return {"hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": reason,
        }}

    return hook


def cfg_timeout(ctx: AppContext) -> float:
    return ctx.cfg.permissions.approval_timeout_s
