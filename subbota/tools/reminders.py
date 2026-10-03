from __future__ import annotations

import json
from typing import TYPE_CHECKING

from claude_agent_sdk import SdkMcpTool, tool

if TYPE_CHECKING:
    from subbota.context import AppContext


def _text(value: str, error: bool = False) -> dict:
    result = {"content": [{"type": "text", "text": value}]}
    if error:
        result["is_error"] = True
    return result


def make_tools(ctx: AppContext) -> list[SdkMcpTool]:
    @tool(
        "add_reminder",
        "Добавить напоминание. when — локальное время ISO YYYY-MM-DDTHH:MM; "
        "recurrence — cron из 5 полей, например 0 9 * * 1-5. "
        'kind="agent": в назначенное время текст станет задачей Субботы, '
        "например «сделай утреннюю сводку погоды и новостей», а результат будет озвучен.",
        {
            "type": "object",
            "properties": {
                "text": {"type": "string", "description": "Текст напоминания или задачи."},
                "when": {"type": ["string", "null"], "description": "Локальное время ISO YYYY-MM-DDTHH:MM."},
                "recurrence": {"type": ["string", "null"], "description": "Cron из 5 полей для повторов."},
                "kind": {"type": "string", "enum": ["say", "agent"], "description": "say — озвучить текст; agent — выполнить задачу."},
            },
            "required": ["text"],
        },
    )
    async def add_reminder(args):
        if ctx.scheduler is None:
            return _text("Планировщик не запущен.", True)
        try:
            reminder = await ctx.scheduler.add(
                args["text"], args.get("when"), args.get("recurrence"), args.get("kind", "say")
            )
            return _text(f"Напоминание №{reminder['id']} добавлено. Следующий запуск: {reminder['next_run']}.")
        except Exception as exc:
            return _text(f"Ошибка: {exc}", True)

    @tool("list_reminders", "Показать активные напоминания.", {})
    async def list_reminders(args):
        if ctx.scheduler is None:
            return _text("Планировщик не запущен.", True)
        try:
            reminders = await ctx.scheduler.list()
            return _text(json.dumps(reminders, ensure_ascii=False) if reminders else "Активных напоминаний нет.")
        except Exception as exc:
            return _text(f"Ошибка: {exc}", True)

    @tool("cancel_reminder", "Отменить напоминание по номеру.", {
        "type": "object",
        "properties": {"id": {"type": "integer", "minimum": 1, "description": "Номер напоминания."}},
        "required": ["id"],
    })
    async def cancel_reminder(args):
        if ctx.scheduler is None:
            return _text("Планировщик не запущен.", True)
        try:
            cancelled = await ctx.scheduler.cancel(args["id"])
            return _text("Напоминание отменено." if cancelled else "Активное напоминание не найдено.", not cancelled)
        except Exception as exc:
            return _text(f"Ошибка: {exc}", True)

    return [add_reminder, list_reminders, cancel_reminder]
