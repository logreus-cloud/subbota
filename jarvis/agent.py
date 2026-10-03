from __future__ import annotations

import asyncio
import json
import logging
import shutil
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING, Any
from uuid import uuid4

from claude_agent_sdk import (
    AssistantMessage,
    ClaudeAgentOptions,
    ClaudeSDKClient,
    ResultMessage,
    SystemMessage,
    TextBlock,
    ToolResultBlock,
    ToolUseBlock,
    UserMessage,
)
from claude_agent_sdk.types import HookMatcher
from jarvis.permissions import make_pre_tool_hook
from jarvis.prompts import build_system_prompt
from jarvis.tools import build_jarvis_server

if TYPE_CHECKING:
    from jarvis.context import AppContext

_LOG = logging.getLogger(__name__)
_CLI_LOG = logging.getLogger("jarvis.cli")
_SOURCES = {"voice": "голос", "text": "панель", "scheduler": "планировщик", "system": "система"}
_DAYS = ("понедельник", "вторник", "среда", "четверг", "пятница", "суббота", "воскресенье")
_TOOLS = ["Bash", "PowerShell", "Read", "Write", "Edit", "Glob", "Grep", "WebSearch", "WebFetch", "TodoWrite"]


@dataclass
class TurnResult:
    turn_id: str
    text: str
    is_error: bool


def _clip(value: Any, limit: int) -> Any:
    if isinstance(value, str):
        return value[:limit]
    if isinstance(value, list):
        return [_clip(item, limit) for item in value]
    if isinstance(value, dict):
        return {key: _clip(item, limit) for key, item in value.items()}
    return value


def _result_text(content: str | list[dict[str, Any]] | None) -> str:
    if isinstance(content, str):
        return content[:4000]
    if content is None:
        return ""
    return "\n".join(
        str(item.get("text", json.dumps(item, ensure_ascii=False, default=str)))
        for item in content
    )[:4000]


class Brain:
    def __init__(self, ctx: AppContext) -> None:
        self.ctx = ctx
        self._queue: asyncio.Queue[tuple[str, str, str, asyncio.Future | None] | None] = asyncio.Queue()
        self._worker_task: asyncio.Task | None = None
        self._client: ClaudeSDKClient | None = None
        self._active: tuple[str, asyncio.Future | None] | None = None
        self._reset_future: asyncio.Future | None = None
        self._interrupted = False
        self._state = "stopped"
        self._backoff = 0

    @property
    def busy(self) -> bool:
        return self._active is not None

    async def start(self) -> None:
        if self._state == "stopping":
            raise RuntimeError("Мозг останавливается")
        if self._state == "running":
            return
        self._queue = asyncio.Queue()
        self._state = "running"
        self._worker_task = asyncio.create_task(self._worker())

    async def ask(self, text: str, source: str = "text") -> TurnResult:
        if self._state != "running":
            return TurnResult("", "Остановлено", True)
        future = asyncio.get_running_loop().create_future()
        turn_id = self._enqueue(text, source, future)
        return await future

    async def submit(self, text: str, source: str = "text") -> str:
        if self._state != "running":
            raise RuntimeError("Мозг остановлен")
        return self._enqueue(text, source, None)

    def _enqueue(self, text: str, source: str, future: asyncio.Future | None) -> str:
        turn_id = uuid4().hex[:12]
        self._queue.put_nowait((turn_id, text, source, future))
        self.ctx.bus.publish("user_message", turn_id=turn_id, text=text, source=source)
        return turn_id

    async def interrupt(self) -> None:
        if self._active is None or self._client is None:
            return
        self._interrupted = True
        try:
            await self._client.interrupt()
        except Exception:
            _LOG.exception("Не удалось прервать ход")

    async def reset(self) -> None:
        if self._state != "running":
            raise RuntimeError("Мозг остановлен")
        future = asyncio.get_running_loop().create_future()
        self._queue.put_nowait(("", "", "__reset__", future))
        await future

    async def stop(self) -> None:
        if self._state == "stopped":
            return
        if self._state == "stopping":
            if self._worker_task is not None:
                await self._worker_task
            return
        self._state = "stopping"
        while not self._queue.empty():
            item = self._queue.get_nowait()
            self._reject(item)
        if self._reset_future is not None and not self._reset_future.done():
            self._reset_future.set_exception(RuntimeError("Остановлено"))
        if self._active is not None:
            turn_id, future = self._active
            if future is not None and not future.done():
                future.set_result(TurnResult(turn_id, "Остановлено", True))
            await self.interrupt()
        if self._worker_task is not None:
            self._queue.put_nowait(None)
            try:
                await asyncio.wait_for(self._worker_task, timeout=5)
            except TimeoutError:
                self._worker_task.cancel()
                try:
                    await self._worker_task
                except asyncio.CancelledError:
                    pass
            self._worker_task = None
        self._state = "stopped"

    def _reject(self, item: tuple[str, str, str, asyncio.Future | None] | None) -> None:
        if item is None or item[3] is None or item[3].done():
            return
        if item[2] == "__reset__":
            item[3].set_exception(RuntimeError("Остановлено"))
        else:
            item[3].set_result(TurnResult(item[0], "Остановлено", True))

    async def _options(self, resume: str | None) -> ClaudeAgentOptions:
        cfg = self.ctx.cfg
        prompt = await asyncio.to_thread(build_system_prompt, self.ctx)
        workspace = await self.ctx.gate.ready()
        servers: dict[str, Any] = {"jarvis": build_jarvis_server(self.ctx)}
        if cfg.browser.enabled:
            if shutil.which(cfg.browser.command):
                servers["playwright"] = {
                    "type": "stdio", "command": cfg.browser.command, "args": cfg.browser.args,
                }
            else:
                _LOG.warning("Браузер отключён: не найдена команда %s", cfg.browser.command)

        async def can_use_tool(name: str, inp: dict, context: Any) -> Any:
            return await self.ctx.gate.check(name, inp, context, "main")

        return ClaudeAgentOptions(
            model=cfg.agent.model,
            effort=cfg.agent.effort,
            system_prompt=prompt,
            tools=_TOOLS,
            mcp_servers=servers,
            can_use_tool=can_use_tool,
            hooks={"PreToolUse": [HookMatcher(
                matcher=None, hooks=[make_pre_tool_hook(self.ctx.gate, "main")],
                timeout=cfg.permissions.approval_timeout_s + 30,
            )]},
            permission_mode="default",
            setting_sources=[],
            cwd=workspace,
            resume=resume,
            stderr=lambda line: _CLI_LOG.warning("%s", line.rstrip()),
        )

    async def _connect(self) -> None:
        # total_cost_usd в SDK накопительный за процесс CLI, считаем разницу.
        self._cost_seen = 0.0
        resume = self.ctx.db.kv_get("session_id")
        client = ClaudeSDKClient(await self._options(resume))
        try:
            await client.connect()
        except Exception:
            await client.disconnect()
            if resume is None:
                raise
            _LOG.warning("Не удалось восстановить сессию; создаётся новая", exc_info=True)
            self.ctx.db.kv_delete("session_id")
            client = ClaudeSDKClient(await self._options(None))
            await client.connect()
        self._client = client
        self._backoff = 0

    async def _disconnect(self) -> None:
        client, self._client = self._client, None
        if client is not None:
            await client.disconnect()

    async def _ensure_connected(self) -> None:
        while self._client is None and self._state == "running":
            if self._backoff:
                await asyncio.sleep(self._backoff)
            if self._state != "running":
                break
            try:
                await self._connect()
            except Exception as exc:
                _LOG.exception("Ошибка подключения к Claude")
                self.ctx.bus.publish("error", message=str(exc))
                self._backoff = min(max(self._backoff * 2, 1), 60)

    async def _worker(self) -> None:
        pending = None
        try:
            while self._state == "running":
                await self._ensure_connected()
                if self._state != "running":
                    break
                stream = self._client.receive_messages()
                message_task = None
                queue_task = None
                parts: list[str] = []
                events: list[tuple[str, dict[str, Any]]] = []
                resetting = False
                failed = False
                try:
                    while self._state == "running":
                        if message_task is None:
                            message_task = asyncio.create_task(anext(stream))
                        if pending is None and queue_task is None:
                            queue_task = asyncio.create_task(self._queue.get())
                        done, _ = await asyncio.wait(
                            {task for task in (message_task, queue_task) if task is not None},
                            return_when=asyncio.FIRST_COMPLETED,
                        )
                        if message_task in done:
                            message = message_task.result()
                            message_task = None
                            self._handle_message(message, parts, events)
                        if queue_task in done:
                            pending = queue_task.result()
                            queue_task = None
                            if pending is None:
                                break
                        if self._state != "running" or pending is None or self._active is not None:
                            continue
                        turn_id, text, source, future = pending
                        pending = None
                        if source == "__reset__":
                            self._reset_future = future
                            resetting = True
                            break
                        self._active = (turn_id, future)
                        self._interrupted = False
                        self.ctx.set_status("thinking")
                        now = datetime.now().astimezone()
                        label = _SOURCES.get(source, source)
                        prefix = f"[{label} · {now:%Y-%m-%d %H:%M}, {_DAYS[now.weekday()]}] "
                        await self._client.query(prefix + text)
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    _LOG.exception("Ошибка потока Claude")
                    self.ctx.bus.publish("error", message=str(exc))
                    if self._active is not None:
                        turn_id, future = self._active
                        result = TurnResult(
                            turn_id, "Прервано" if self._interrupted else str(exc), True
                        )
                        self.ctx.bus.publish(
                            "turn_done", turn_id=turn_id, result=result.text,
                            is_error=True, cost_usd=None, duration_ms=0,
                        )
                        if future is not None and not future.done():
                            future.set_result(result)
                        self._active = None
                        self.ctx.set_status("idle")
                    self._backoff = 1
                    failed = True
                finally:
                    for task in (message_task, queue_task):
                        if task is not None and not task.done():
                            task.cancel()
                    await asyncio.gather(
                        *(task for task in (message_task, queue_task) if task is not None),
                        return_exceptions=True,
                    )
                    if queue_task is not None and not queue_task.cancelled():
                        pending = queue_task.result()
                    await stream.aclose()
                if resetting:
                    try:
                        await self._disconnect()
                        self.ctx.db.kv_delete("session_id")
                        self.ctx.gate.clear_session_rules()
                        await self._ensure_connected()
                        if self._state != "running":
                            raise RuntimeError("Остановлено")
                        self.ctx.bus.publish("session_reset")
                        if self._reset_future is not None and not self._reset_future.done():
                            self._reset_future.set_result(None)
                    except asyncio.CancelledError:
                        raise
                    except Exception as exc:
                        if self._reset_future is not None and not self._reset_future.done():
                            self._reset_future.set_exception(exc)
                    finally:
                        self._reset_future = None
                elif failed:
                    try:
                        await self._disconnect()
                    except Exception:
                        _LOG.exception("Ошибка отключения Claude")
        finally:
            self._reject(pending)
            while not self._queue.empty():
                self._reject(self._queue.get_nowait())
            if self._reset_future is not None and not self._reset_future.done():
                self._reset_future.set_exception(RuntimeError("Остановлено"))
            if self._active is not None:
                turn_id, future = self._active
                if future is not None and not future.done():
                    future.set_result(TurnResult(turn_id, "Остановлено", True))
                self._active = None
                self.ctx.set_status("idle")
            try:
                await self._disconnect()
            except Exception:
                _LOG.exception("Ошибка отключения Claude")
            if self._state == "running":
                self._state = "stopped"

    def _handle_message(
        self, message: Any, parts: list[str], events: list[tuple[str, dict[str, Any]]]
    ) -> None:
        if isinstance(message, AssistantMessage):
            for block in message.content:
                if isinstance(block, TextBlock):
                    parts.append(block.text)
                elif isinstance(block, ToolUseBlock):
                    events.append(("tool_call", {
                        "tool_use_id": block.id, "name": block.name,
                        "input": _clip(block.input, 2000),
                    }))
        elif isinstance(message, UserMessage) and isinstance(message.content, list):
            for block in message.content:
                if isinstance(block, ToolResultBlock):
                    events.append(("tool_result", {
                        "tool_use_id": block.tool_use_id, "is_error": bool(block.is_error),
                        "text": _result_text(block.content),
                    }))
        elif isinstance(message, ResultMessage):
            own_result = (
                self._active is not None
                and (message.origin is None or message.origin["kind"] == "human")
            )
            turn_id = self._active[0] if own_result else "system"
            for event, payload in events:
                self.ctx.bus.publish(event, turn_id=turn_id, **payload)
            if own_result:
                future = self._active[1]
                if message.session_id:
                    self.ctx.db.kv_set("session_id", message.session_id)
                for part in parts:
                    self.ctx.bus.publish("assistant_message", turn_id=turn_id, text=part)
                interrupted = self._interrupted or message.terminal_reason in {
                    "aborted_streaming", "aborted_tools",
                }
                result = "Прервано" if interrupted else message.result or "".join(parts)
                is_error = interrupted or message.is_error
                total = message.total_cost_usd
                cost = None
                if total is not None:
                    seen = getattr(self, "_cost_seen", 0.0)
                    cost = total - seen if total >= seen else total
                    self._cost_seen = total
                self.ctx.bus.publish(
                    "turn_done", turn_id=turn_id, result=result, is_error=is_error,
                    cost_usd=cost, duration_ms=message.duration_ms,
                )
                if future is not None and not future.done():
                    future.set_result(TurnResult(turn_id, result, is_error))
                self._active = None
                self.ctx.set_status("idle")
            else:
                text = message.result or "".join(parts)
                if text:
                    self.ctx.bus.publish("assistant_message", turn_id="system", text=text)
            parts.clear()
            events.clear()
