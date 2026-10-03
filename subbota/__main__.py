from __future__ import annotations

import argparse
import asyncio
import logging
import signal
import sys
import threading
import webbrowser
from logging.handlers import RotatingFileHandler
from pathlib import Path

import uvicorn
import win32api
import win32event
import winerror

from subbota.config import Config, load_config
from subbota.cuda import add_nvidia_dll_dirs

_LOG = logging.getLogger(__name__)


def _logging(cfg: Config) -> None:
    handler = RotatingFileHandler(cfg.data_dir / "subbota.log", maxBytes=5 * 1024 * 1024, backupCount=3, encoding="utf-8")
    handlers = [handler]
    if sys.stderr is not None:
        handlers.append(logging.StreamHandler(sys.stderr))
    logging.basicConfig(level=getattr(logging, cfg.general.log_level.upper(), logging.INFO), format="%(asctime)s %(levelname)s %(name)s: %(message)s", handlers=handlers, force=True)


async def _stop(name, action):
    try:
        await asyncio.wait_for(action(), timeout=10)
    except asyncio.CancelledError:
        _LOG.warning("Задача отменена при остановке: %s", name)
    except Exception:
        _LOG.exception("Ошибка остановки: %s", name)


async def _server_result(task):
    try:
        await task
    except asyncio.CancelledError:
        if asyncio.current_task().cancelling():
            raise
        _LOG.warning("Задача сервера отменена")


async def _cancel_code_tasks(manager):
    tasks = []
    for item in manager._tasks.values():
        if item["status"] == "running":
            manager.cancel(item["id"])
            tasks.append(item["asyncio_task"])
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)


async def main(cfg: Config, no_voice: bool = False, no_tray: bool = False) -> None:
    from subbota.agent import Brain
    from subbota.context import AppContext
    from subbota.db import Database
    from subbota.events import EventBus
    from subbota.permissions import PermissionGate
    from subbota.scheduler import ReminderScheduler
    from subbota.server import create_app
    from subbota.tools.code import CodeTaskManager

    loop = asyncio.get_running_loop()
    db = Database(cfg.data_dir)
    bus = EventBus(db, loop)
    ctx = AppContext(cfg, bus, db, None, loop)
    ctx.gate = PermissionGate(ctx)
    ctx.scheduler = ReminderScheduler(ctx)
    ctx.code_tasks = CodeTaskManager(ctx)
    ctx.brain = Brain(ctx)
    if cfg.voice.enabled and not no_voice:
        from subbota.voice.loop import VoiceLoop
        ctx.voice = VoiceLoop(ctx)

    stop_event = asyncio.Event()
    handlers = {}
    for name in ("SIGINT", "SIGTERM", "SIGBREAK"):
        sig = getattr(signal, name, None)
        if sig is None:
            continue
        try:
            handlers[sig] = signal.getsignal(sig)
            signal.signal(sig, lambda number, frame: loop.call_soon_threadsafe(stop_event.set))
        except (OSError, ValueError):
            pass

    server = None
    server_task = None
    tray = None
    try:
        await ctx.scheduler.start()
        await ctx.brain.start()
        if ctx.voice is not None:
            ctx.voice.start()
        if not no_tray:
            from subbota.tray import Tray
            tray = Tray(ctx, lambda: loop.call_soon_threadsafe(stop_event.set))
            tray.start()
        server = uvicorn.Server(uvicorn.Config(create_app(ctx), host="127.0.0.1", port=cfg.server.port, log_config=None, ws="websockets"))
        server_task = asyncio.create_task(server.serve())
        stop_task = asyncio.create_task(stop_event.wait())
        try:
            while not server.started:
                done, _ = await asyncio.wait({server_task, stop_task}, timeout=0.05, return_when=asyncio.FIRST_COMPLETED)
                if server_task in done:
                    await _server_result(server_task)
                    return
                if stop_task in done:
                    return
            if server_task.done():
                await _server_result(server_task)
                return
            if stop_task.done():
                return
            if cfg.server.open_browser_on_start:
                webbrowser.open(f"http://127.0.0.1:{cfg.server.port}")
            if ctx.voice is not None:
                await ctx.announce("Суббота на связи.", "info")
            await asyncio.wait({server_task, stop_task}, return_when=asyncio.FIRST_COMPLETED)
            if server_task.done():
                await _server_result(server_task)
        finally:
            stop_task.cancel()
            await asyncio.gather(stop_task, return_exceptions=True)
    finally:
        if server is not None:
            server.should_exit = True

        async def cleanup():
            if server_task is not None:
                await _stop("сервер", lambda: server_task)
            if ctx.voice is not None:
                await _stop("голос", lambda: _run_sync(ctx.voice.stop))
            if tray is not None:
                await _stop("трей", lambda: _run_sync(tray.stop))
            await _stop("планировщик", lambda: _shutdown_scheduler(ctx.scheduler))
            await _stop("задачи по коду", lambda: _cancel_code_tasks(ctx.code_tasks))
            await _stop("мозг", ctx.brain.stop)
            await _stop("база данных", lambda: _run_sync(db.close))
            for sig, handler in handlers.items():
                try:
                    signal.signal(sig, handler)
                except (OSError, ValueError):
                    pass

        cleanup_task = asyncio.create_task(cleanup())
        cancelled = False
        while not cleanup_task.done():
            try:
                await asyncio.shield(cleanup_task)
            except asyncio.CancelledError:
                cancelled = True
        await cleanup_task
        if cancelled:
            raise asyncio.CancelledError


async def _shutdown_scheduler(scheduler):
    scheduler.shutdown()


async def _run_sync(callback):
    errors = []
    finished = threading.Event()

    def run():
        try:
            callback()
        except Exception as exc:
            errors.append(exc)
        finally:
            finished.set()

    thread = threading.Thread(target=run, name="subbota-stop", daemon=True)
    thread.start()
    while not finished.is_set():
        await asyncio.sleep(0.05)
    thread.join()
    if errors:
        raise errors[0]


def cli() -> None:
    parser = argparse.ArgumentParser(description="Локальный голосовой ассистент Суббота")
    parser.add_argument("--no-voice", action="store_true")
    parser.add_argument("--no-tray", action="store_true")
    parser.add_argument("--config", type=Path)
    args = parser.parse_args()
    cfg = load_config(args.config)
    _logging(cfg)
    mutex = win32event.CreateMutex(None, False, r"Global\SubbotaAssistantMutex")
    if win32api.GetLastError() == winerror.ERROR_ALREADY_EXISTS:
        webbrowser.open(f"http://127.0.0.1:{cfg.server.port}")
        win32api.CloseHandle(mutex)
        return
    try:
        add_nvidia_dll_dirs()
        asyncio.run(main(cfg, args.no_voice, args.no_tray))
    finally:
        win32api.CloseHandle(mutex)


if __name__ == "__main__":
    cli()
