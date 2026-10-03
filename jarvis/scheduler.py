from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta
from typing import TYPE_CHECKING

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.date import DateTrigger
from tzlocal import get_localzone

if TYPE_CHECKING:
    from jarvis.context import AppContext


_LOG = logging.getLogger(__name__)


class ReminderScheduler:
    def __init__(self, ctx: AppContext) -> None:
        self.ctx = ctx
        self.tz = get_localzone()
        self._scheduler = AsyncIOScheduler(timezone=self.tz)
        self._started = False
        self._locks: dict[int, asyncio.Lock] = {}
        self._running: dict[int, asyncio.Task] = {}

    async def start(self) -> None:
        if self._started:
            return
        self._scheduler.start()
        self._started = True
        reminders = await asyncio.to_thread(self.ctx.db.list_reminders)
        now = datetime.now(self.tz)
        changed = False
        for reminder in reminders:
            try:
                run_at = self._parse_stored(reminder["run_at"]) if reminder["run_at"] else None
                if reminder["recurrence"] is None and run_at is not None and run_at < now:
                    if now - run_at < timedelta(hours=12):
                        self._schedule(reminder, missed=True)
                    else:
                        await asyncio.to_thread(self.ctx.db.update_reminder, reminder["id"], status="done")
                        changed = True
                else:
                    self._schedule(reminder)
            except Exception:
                _LOG.exception("Не удалось запланировать напоминание %s", reminder["id"])
        if changed:
            await asyncio.to_thread(self.ctx.bus.publish, "reminders_changed")

    def _parse_stored(self, value: str) -> datetime:
        result = datetime.fromisoformat(value)
        return result.replace(tzinfo=self.tz) if result.tzinfo is None else result.astimezone(self.tz)

    def _trigger(self, reminder: dict, missed: bool = False):
        if reminder["recurrence"]:
            return CronTrigger.from_crontab(reminder["recurrence"], timezone=self.tz)
        run_at = datetime.now(self.tz) if missed else self._parse_stored(reminder["run_at"])
        return DateTrigger(run_date=run_at, timezone=self.tz)

    def _schedule(self, reminder: dict, missed: bool = False) -> None:
        self._scheduler.add_job(
            self._fire,
            trigger=self._trigger(reminder, missed),
            args=[reminder["id"], missed],
            id=f"rem-{reminder['id']}",
            replace_existing=True,
            misfire_grace_time=3600,
            coalesce=True,
        )

    def _with_next_run(self, reminder: dict) -> dict:
        result = dict(reminder)
        job = self._scheduler.get_job(f"rem-{reminder['id']}") if self._started else None
        result["next_run"] = job.next_run_time.isoformat() if job and job.next_run_time else None
        return result

    async def add(
        self,
        text: str,
        when: str | None = None,
        recurrence: str | None = None,
        kind: str = "say",
    ) -> dict:
        if not isinstance(text, str) or not text.strip():
            raise ValueError("Текст напоминания не должен быть пустым.")
        if kind not in {"say", "agent"}:
            raise ValueError("Тип напоминания: say или agent.")
        if when is not None and (not isinstance(when, str) or not when.strip()):
            raise ValueError("Время должно быть строкой ISO YYYY-MM-DDTHH:MM.")
        if recurrence is not None and (not isinstance(recurrence, str) or not recurrence.strip()):
            raise ValueError("Повтор должен быть cron-выражением из 5 полей.")
        if not when and not recurrence:
            raise ValueError("Укажите время when или повтор recurrence.")
        if when and recurrence:
            raise ValueError("Укажите либо время when, либо повтор recurrence.")
        run_at = None
        if when:
            try:
                run_at = datetime.fromisoformat(when)
            except ValueError as exc:
                raise ValueError("Неверное время. Используйте ISO YYYY-MM-DDTHH:MM.") from exc
            if run_at.tzinfo is None:
                run_at = run_at.replace(tzinfo=self.tz)
            else:
                run_at = run_at.astimezone(self.tz)
            if run_at < datetime.now(self.tz) - timedelta(seconds=5):
                raise ValueError("Время напоминания должно быть в будущем.")
        if recurrence:
            try:
                trigger = CronTrigger.from_crontab(recurrence, timezone=self.tz)
                if trigger.get_next_fire_time(None, datetime.now(self.tz)) is None:
                    raise ValueError("Повтор не даёт будущих запусков.")
            except (TypeError, ValueError) as exc:
                raise ValueError(f"Неверный cron-повтор: {exc}") from exc
        if not self._started:
            await self.start()
        reminder_id = await asyncio.to_thread(
            self.ctx.db.add_reminder, text.strip(), kind,
            run_at.isoformat(timespec="seconds") if run_at else None, recurrence,
        )
        reminder = await asyncio.to_thread(self.ctx.db.get_reminder, reminder_id)
        try:
            self._schedule(reminder)
        except Exception:
            await asyncio.to_thread(self.ctx.db.update_reminder, reminder_id, status="cancelled")
            raise
        await asyncio.to_thread(self.ctx.bus.publish, "reminders_changed")
        return self._with_next_run(reminder)

    async def cancel(self, id: int) -> bool:
        if type(id) is not int or id < 1:
            raise ValueError("Номер напоминания должен быть положительным целым числом.")
        async with self._locks.setdefault(id, asyncio.Lock()):
            reminder = await asyncio.to_thread(self.ctx.db.get_reminder, id)
            if reminder is None or reminder["status"] != "active":
                return False
            await asyncio.to_thread(self.ctx.db.update_reminder, id, status="cancelled")
            if self._started:
                job = self._scheduler.get_job(f"rem-{id}")
                if job is not None:
                    job.remove()
            running = self._running.get(id)
            if running is not None:
                running.cancel()
        await asyncio.to_thread(self.ctx.bus.publish, "reminders_changed")
        return True

    async def list(self) -> list[dict]:
        reminders = await asyncio.to_thread(self.ctx.db.list_reminders)
        return [self._with_next_run(reminder) for reminder in reminders]

    async def _fire(self, id: int, missed: bool = False) -> None:
        running = asyncio.current_task()
        if running is not None:
            self._running[id] = running
        try:
            async with self._locks.setdefault(id, asyncio.Lock()):
                reminder = await asyncio.to_thread(self.ctx.db.get_reminder, id)
                if reminder is None or reminder["status"] != "active":
                    return
            if reminder["kind"] == "agent":
                if self.ctx.brain is None:
                    raise RuntimeError("Джарвис не запущен.")
                result = await self.ctx.brain.ask(reminder["text"], "scheduler")
                if result.is_error:
                    raise RuntimeError(result.text or "Задача завершилась с ошибкой.")
                message = f"Пропущенное напоминание: {result.text}" if missed else result.text
            else:
                prefix = "Пропущенное напоминание" if missed else "Напоминание"
                message = f"{prefix}: {reminder['text']}"
            async with self._locks[id]:
                current = await asyncio.to_thread(self.ctx.db.get_reminder, id)
                if current is None or current["status"] != "active":
                    return
            await self.ctx.announce(message, "reminder")
            async with self._locks[id]:
                current = await asyncio.to_thread(self.ctx.db.get_reminder, id)
                if current is None or current["status"] != "active":
                    return
                if reminder["recurrence"] is None:
                    await asyncio.to_thread(self.ctx.db.update_reminder, id, status="done")
                await asyncio.to_thread(self.ctx.bus.publish, "reminders_changed")
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            _LOG.exception("Ошибка напоминания %s", id)
            await asyncio.to_thread(self.ctx.bus.publish, "error", message=f"Ошибка напоминания {id}: {exc}")
        finally:
            if self._running.get(id) is running:
                self._running.pop(id, None)

    def shutdown(self) -> None:
        if self._started:
            self._scheduler.shutdown(wait=False)
            self._started = False
