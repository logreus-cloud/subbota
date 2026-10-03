from __future__ import annotations

import asyncio
import json
from dataclasses import asdict
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from jarvis.tools.memory import _LOCK, _write


class ReminderBody(BaseModel):
    text: str
    when: str | None = None
    recurrence: str | None = None
    kind: str = "say"


class MemoryBody(BaseModel):
    text: str = Field(max_length=50_000)


def _strings(value):
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, dict):
        return {key: _strings(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_strings(item) for item in value]
    return value


def create_app(ctx) -> FastAPI:
    app = FastAPI()
    port = ctx.cfg.server.port
    origins = {f"http://{host}:{number}" for host in ("127.0.0.1", "localhost") for number in (port, 5173)}

    @app.middleware("http")
    async def check_origin(request: Request, call_next):
        origin = request.headers.get("origin")
        if request.method != "GET" and request.url.path.startswith("/api/") and origin and origin not in origins:
            return JSONResponse({"detail": "Недопустимый Origin"}, status_code=403)
        return await call_next(request)

    @app.websocket("/ws")
    async def websocket(websocket: WebSocket):
        if websocket.headers.get("origin") not in origins:
            await websocket.close(code=1008)
            return
        await websocket.accept()
        queue = ctx.bus.subscribe()
        try:
            history = await asyncio.to_thread(ctx.db.events, None, 300)
            snapshot_id = history[-1]["id"] if history else 0
            reset_floor = await asyncio.to_thread(ctx.db.last_event_id, "session_reset")
            await websocket.send_json({
                "type": "snapshot",
                "state": dict(ctx.state),
                "history": history,
                "reset_floor": reset_floor,
                "approvals": ctx.gate.pending() if ctx.gate else [],
                "reminders": await ctx.scheduler.list() if ctx.scheduler else [],
                "code_tasks": ctx.code_tasks.list() if ctx.code_tasks else [],
            })

            async def send_events():
                while True:
                    event = await queue.get()
                    if event["id"] == 0 or event["id"] > snapshot_id:
                        await websocket.send_json(event)

            async def receive_commands():
                while True:
                    try:
                        message = json.loads(await websocket.receive_text())
                        if not isinstance(message, dict):
                            raise ValueError("Ожидается объект JSON.")
                        command = message.get("type")
                        if command == "chat":
                            text = message.get("text")
                            if not isinstance(text, str) or len(text) > 20_000:
                                raise ValueError("Текст должен быть строкой длиной до 20000 символов.")
                            if text.strip():
                                await ctx.brain.submit(text, "text")
                        elif command == "approve":
                            if not isinstance(message.get("approval_id"), str) or type(message.get("allow")) is not bool or type(message.get("remember", False)) is not bool:
                                raise ValueError("Неверные параметры подтверждения.")
                            ctx.gate.resolve(message["approval_id"], message["allow"], message.get("remember", False), by="ui")
                        elif command == "interrupt":
                            await ctx.brain.interrupt()
                            if ctx.voice is not None:
                                ctx.voice.stop_speaking()
                        elif command == "reset":
                            await ctx.brain.reset()
                        elif command == "mute":
                            if type(message.get("muted")) is not bool:
                                raise ValueError("Параметр muted должен быть логическим.")
                            ctx.set_mic_muted(message["muted"])
                        elif command == "speak":
                            text = message.get("text")
                            if not isinstance(text, str) or len(text) > 20_000:
                                raise ValueError("Текст должен быть строкой длиной до 20000 символов.")
                            if ctx.voice is not None:
                                ctx.voice.say(text)
                        else:
                            raise ValueError("Неизвестная команда.")
                    except WebSocketDisconnect:
                        return
                    except (ValueError, TypeError) as exc:
                        await websocket.send_json({"type": "error", "message": str(exc)})
                    except Exception as exc:
                        await websocket.send_json({"type": "error", "message": str(exc)})

            sender = asyncio.create_task(send_events())
            receiver = asyncio.create_task(receive_commands())
            try:
                await asyncio.wait({sender, receiver}, return_when=asyncio.FIRST_COMPLETED)
            finally:
                sender.cancel()
                receiver.cancel()
                await asyncio.gather(sender, receiver, return_exceptions=True)
        finally:
            ctx.bus.unsubscribe(queue)

    @app.get("/api/history")
    async def history(before: int | None = None, limit: int = Query(200, ge=1, le=1000)):
        return await asyncio.to_thread(ctx.db.events, before, limit)

    @app.get("/api/reminders")
    async def reminders():
        return await ctx.scheduler.list() if ctx.scheduler else []

    @app.post("/api/reminders")
    async def add_reminder(body: ReminderBody):
        if ctx.scheduler is None:
            raise HTTPException(503, "Планировщик недоступен")
        try:
            return await ctx.scheduler.add(body.text, body.when, body.recurrence, body.kind)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc

    @app.delete("/api/reminders/{id}")
    async def delete_reminder(id: int):
        if ctx.scheduler is None:
            raise HTTPException(503, "Планировщик недоступен")
        try:
            return {"cancelled": await ctx.scheduler.cancel(id)}
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc

    @app.get("/api/memory")
    async def memory():
        path = ctx.cfg.data_dir / "memory.md"
        try:
            text = await asyncio.to_thread(path.read_text, encoding="utf-8")
        except FileNotFoundError:
            text = ""
        return {"text": text}

    @app.put("/api/memory")
    async def update_memory(body: MemoryBody):
        path = ctx.cfg.data_dir / "memory.md"
        def save():
            with _LOCK:
                _write(path, body.text.splitlines())
        await asyncio.to_thread(save)
        return {"text": body.text}

    @app.get("/api/projects")
    async def projects():
        return await asyncio.to_thread(ctx.code_tasks.projects) if ctx.code_tasks else []

    @app.get("/api/code-tasks")
    async def code_tasks():
        return ctx.code_tasks.list() if ctx.code_tasks else []

    @app.post("/api/code-tasks/{id}/cancel")
    async def cancel_code_task(id: str):
        return {"cancelled": ctx.code_tasks.cancel(id) if ctx.code_tasks else False}

    @app.get("/api/config")
    async def config():
        return _strings(asdict(ctx.cfg))

    dist = Path(__file__).resolve().parent.parent / "web" / "dist"
    if (dist / "index.html").is_file():
        if (dist / "assets").is_dir():
            app.mount("/assets", StaticFiles(directory=dist / "assets"), name="assets")
        @app.get("/")
        async def index():
            return FileResponse(dist / "index.html")
        @app.get("/{path:path}")
        async def fallback(path: str):
            if path == "api" or path.startswith("api/"):
                raise HTTPException(404)
            file = (dist / path).resolve()
            if file.is_relative_to(dist.resolve()) and file.is_file():
                return FileResponse(file)
            return FileResponse(dist / "index.html")
    else:
        @app.get("/")
        async def unbuilt():
            return HTMLResponse("<!doctype html><html lang='ru'><meta charset='utf-8'><body>Панель не собрана: выполните <code>npm run build</code> в папке web.</body></html>")
    return app
