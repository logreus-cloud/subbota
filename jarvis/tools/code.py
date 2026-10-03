from __future__ import annotations

import asyncio
import json
import logging
import re
import stat
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Annotated
from uuid import uuid4

from claude_agent_sdk import ClaudeAgentOptions, ResultMessage, SdkMcpTool, query, tool
from claude_agent_sdk.types import HookMatcher

from jarvis.permissions import CodeScope, make_pre_tool_hook

if TYPE_CHECKING:
    from jarvis.context import AppContext


_LOG = logging.getLogger(__name__)


def _text(value: str, error: bool = False) -> dict:
    result = {"content": [{"type": "text", "text": value}]}
    if error:
        result["is_error"] = True
    return result


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _short_summary(summary: str) -> str:
    sentences = re.split(r"(?<=[.!?])\s+", summary.strip())
    return " ".join(sentences[:2])[:600]


class CodeTaskManager:
    def __init__(self, ctx: AppContext) -> None:
        self.ctx = ctx
        self._tasks: dict[str, dict] = {}

    def projects(self) -> list[dict]:
        projects = []
        for root in self.ctx.cfg.code.workspace_roots:
            root = Path(root).expanduser().resolve()
            if not root.is_dir():
                continue
            for path in root.iterdir():
                if path.name.startswith(".") or not path.is_dir():
                    continue
                if getattr(path.stat(), "st_file_attributes", 0) & getattr(stat, "FILE_ATTRIBUTE_HIDDEN", 0):
                    continue
                resolved = path.resolve()
                if resolved.is_relative_to(root) and resolved != root:
                    projects.append({"name": path.name, "path": str(resolved)})
        return sorted(projects, key=lambda item: (item["name"].casefold(), item["path"].casefold()))

    def resolve(self, project: str) -> Path:
        if not isinstance(project, str) or not project.strip():
            raise ValueError("Укажите название или абсолютный путь проекта.")
        project = project.strip()
        path = Path(project).expanduser()
        if path.is_absolute():
            resolved = path.resolve()
            for root in self.ctx.cfg.code.workspace_roots:
                root = Path(root).expanduser().resolve()
                if resolved != root and resolved.is_relative_to(root) and resolved.is_dir():
                    return resolved
            raise ValueError("Проект должен быть папкой внутри разрешённого рабочего каталога.")
        matches = [item for item in self.projects() if item["name"].casefold() == project.casefold()]
        if not matches:
            raise ValueError(f"Проект «{project}» не найден.")
        if len(matches) > 1:
            raise ValueError("Есть несколько проектов с таким именем. Укажите абсолютный путь.")
        return Path(matches[0]["path"])

    async def start(self, project: str, task: str) -> dict:
        if not isinstance(task, str) or not task.strip():
            raise ValueError("Опишите задачу по коду.")
        path = await asyncio.to_thread(self.resolve, project)
        if sum(item["status"] == "running" for item in self._tasks.values()) >= self.ctx.cfg.code.max_parallel:
            raise ValueError("Уже запущено максимальное число задач по коду.")
        task_id = uuid4().hex[:8]
        while task_id in self._tasks:
            task_id = uuid4().hex[:8]
        item = {
            "id": task_id,
            "project": path.name,
            "path": str(path),
            "task": task.strip(),
            "status": "running",
            "summary": "",
            "started_at": _now(),
            "finished_at": None,
            "cost_usd": None,
        }
        self._tasks[task_id] = item
        publication = asyncio.create_task(asyncio.to_thread(self._publish, item.copy()))
        try:
            await asyncio.shield(publication)
        except asyncio.CancelledError:
            try:
                await publication
            except Exception:
                _LOG.exception("Ошибка публикации задачи по коду %s", task_id)
            await self._finish_cancelled(item)
            raise
        except BaseException:
            self._tasks.pop(task_id, None)
            raise
        if item["status"] == "running":
            running = asyncio.create_task(self._run(item))
            item["asyncio_task"] = running
            running.add_done_callback(lambda finished: self._on_done(item, finished))
        else:
            await self._finish_cancelled(item)
        return self._public(item)

    async def _finish_cancelled(self, item: dict) -> None:
        if item["finished_at"] is not None:
            return
        item["status"] = "cancelled"
        item["summary"] = "Задача отменена."
        item["finished_at"] = _now()
        await asyncio.to_thread(self._publish, item.copy())

    def _on_done(self, item: dict, task: asyncio.Task) -> None:
        if task.cancelled() and item["finished_at"] is None:
            item["status"] = "cancelled"
            item["summary"] = "Задача отменена."
            item["finished_at"] = _now()
            asyncio.create_task(asyncio.to_thread(self._publish, item.copy()))

    def _publish(self, item: dict) -> None:
        self.ctx.bus.publish(
            "code_task", task_id=item["id"], project=item["project"],
            task=item["task"], status=item["status"], summary=item["summary"],
        )

    async def _run(self, item: dict) -> None:
        task_id = item["id"]
        project_dir = Path(item["path"])
        scope = CodeScope(task_id, project_dir)

        async def can_use_tool(name, tool_input, context):
            return await self.ctx.gate.check(name, tool_input, context, scope)

        options = ClaudeAgentOptions(
            cwd=project_dir,
            model=self.ctx.cfg.agent.model,
            effort=self.ctx.cfg.code.effort,
            system_prompt={"type": "preset", "preset": "claude_code"},
            setting_sources=["project"],
            permission_mode="default",
            can_use_tool=can_use_tool,
            hooks={"PreToolUse": [HookMatcher(
                matcher=None, hooks=[make_pre_tool_hook(self.ctx.gate, scope)],
                timeout=self.ctx.cfg.permissions.approval_timeout_s + 30,
            )]},
            max_budget_usd=self.ctx.cfg.code.max_budget_usd,
        )
        prompt = (
            f"{item['task']}\n\n"
            "В конце дай итог в 2–4 предложениях по-русски: что сделано, "
            "какие файлы изменены и что проверено."
        )
        try:
            result = None
            async for message in query(prompt=prompt, options=options):
                if isinstance(message, ResultMessage):
                    result = message
            if result is None:
                raise RuntimeError("Задача завершилась без результата.")
            item["summary"] = result.result or ("Задача завершилась с ошибкой." if result.is_error else "Задача завершена.")
            item["cost_usd"] = result.total_cost_usd
            item["status"] = "failed" if result.is_error else "done"
        except asyncio.CancelledError:
            if item["status"] != "cancelled":
                item["status"] = "cancelled"
                item["summary"] = "Задача отменена."
            raise
        except Exception as exc:
            _LOG.exception("Ошибка задачи по коду %s", task_id)
            item["status"] = "failed"
            item["summary"] = f"Ошибка: {exc}"
        finally:
            item["finished_at"] = _now()
            await asyncio.to_thread(self._publish, item.copy())
            if item["status"] != "cancelled":
                await self.ctx.announce(
                    f"Задача в проекте {item['project']} завершена. {_short_summary(item['summary'])}",
                    "code_task",
                )

    def cancel(self, task_id: str) -> bool:
        item = self._tasks.get(task_id)
        if item is None or item["status"] != "running":
            return False
        item["status"] = "cancelled"
        item["summary"] = "Задача отменена."
        running = item.get("asyncio_task")
        if running is not None:
            running.cancel()
        return True

    def _public(self, item: dict) -> dict:
        return {key: value for key, value in item.items() if key not in {"task", "asyncio_task"}}

    def list(self) -> list[dict]:
        return [self._public(item) for item in self._tasks.values()]


def make_tools(ctx: AppContext) -> list[SdkMcpTool]:
    @tool("list_projects", "Показать доступные проекты для задач по коду.", {})
    async def list_projects(args):
        if ctx.code_tasks is None:
            return _text("Менеджер задач по коду не запущен.", True)
        try:
            projects = await asyncio.to_thread(ctx.code_tasks.projects)
            return _text(json.dumps(projects, ensure_ascii=False) if projects else "Проекты не найдены.")
        except Exception as exc:
            return _text(f"Ошибка: {exc}", True)

    @tool("code_task", "Запустить задачу по коду в фоне. Сразу сообщи пользователю, что задача запущена.", {
        "project": Annotated[str, "Название или абсолютный путь проекта."],
        "task": Annotated[str, "Что сделать в проекте."],
    })
    async def code_task(args):
        if ctx.code_tasks is None:
            return _text("Менеджер задач по коду не запущен.", True)
        try:
            item = await ctx.code_tasks.start(args["project"], args["task"])
            return _text(f"Задача {item['id']} запущена в проекте {item['project']}.")
        except Exception as exc:
            return _text(f"Ошибка: {exc}", True)

    @tool("code_task_status", "Показать состояние одной или всех задач по коду.", {
        "type": "object",
        "properties": {"task_id": {"type": ["string", "null"], "description": "Номер задачи; без него показать все."}},
    })
    async def code_task_status(args):
        if ctx.code_tasks is None:
            return _text("Менеджер задач по коду не запущен.", True)
        items = ctx.code_tasks.list()
        task_id = args.get("task_id")
        if task_id is not None:
            items = [item for item in items if item["id"] == task_id]
        return _text(json.dumps(items, ensure_ascii=False) if items else "Задачи не найдены.", bool(task_id and not items))

    @tool("cancel_code_task", "Отменить выполняемую задачу по коду.", {
        "task_id": Annotated[str, "Восьмизначный номер задачи."],
    })
    async def cancel_code_task(args):
        if ctx.code_tasks is None:
            return _text("Менеджер задач по коду не запущен.", True)
        try:
            cancelled = ctx.code_tasks.cancel(args["task_id"])
            return _text("Задача отменяется." if cancelled else "Выполняемая задача не найдена.", not cancelled)
        except Exception as exc:
            return _text(f"Ошибка: {exc}", True)

    return [list_projects, code_task, code_task_status, cancel_code_task]
