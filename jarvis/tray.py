from __future__ import annotations

import asyncio
import logging
import threading
import webbrowser

import pystray
from PIL import Image, ImageDraw

_LOG = logging.getLogger(__name__)
_COLORS = {
    "idle": "#52c9ff", "listening": "#4ddf83", "recording": "#4ddf83",
    "thinking": "#ffd359", "transcribing": "#ffd359", "speaking": "#ad78ff",
}


class Tray:
    def __init__(self, ctx, on_quit):
        self.ctx = ctx
        self.on_quit = on_quit
        self._thread: threading.Thread | None = None
        self._task: asyncio.Task | None = None
        self._queue = None
        self.icon = pystray.Icon("JarvisAssistant", self._image(), "Джарвис", pystray.Menu(
            pystray.MenuItem("Открыть панель", self._open, default=True),
            pystray.MenuItem("Микрофон", self._mute, checked=lambda item: not self.ctx.state["mic_muted"]),
            pystray.MenuItem("Новый разговор", self._reset),
            pystray.MenuItem("Прервать", self._interrupt),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Выход", self._quit),
        ))

    def _image(self):
        muted = self.ctx.state["mic_muted"]
        status = self.ctx.state["status"]
        color = "#87919b" if muted else _COLORS.get(status, _COLORS["idle"])
        image = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
        draw = ImageDraw.Draw(image)
        draw.ellipse((3, 3, 60, 60), fill="#101923")
        draw.ellipse((10, 10, 53, 53), outline=color, width=6)
        draw.ellipse((23, 23, 40, 40), fill=color)
        return image

    def _open(self, icon, item):
        webbrowser.open(f"http://127.0.0.1:{self.ctx.cfg.server.port}")

    def _mute(self, icon, item):
        self.ctx.loop.call_soon_threadsafe(self._toggle_mic)

    def _toggle_mic(self):
        self.ctx.set_mic_muted(not self.ctx.state["mic_muted"])

    def _reset(self, icon, item):
        if self.ctx.brain is not None:
            self.ctx.loop.call_soon_threadsafe(self.ctx.loop.create_task, self.ctx.brain.reset())

    def _interrupt(self, icon, item):
        if self.ctx.brain is not None:
            self.ctx.loop.call_soon_threadsafe(self.ctx.loop.create_task, self.ctx.brain.interrupt())
        if self.ctx.voice is not None:
            self.ctx.voice.speaker.stop()

    def _quit(self, icon, item):
        self.on_quit()

    async def _events(self):
        queue = self._queue
        try:
            while True:
                event = await queue.get()
                if event["type"] == "status":
                    self.icon.icon = self._image()
                    self.icon.update_menu()
                elif event["type"] == "announcement" and event.get("kind") in {"reminder", "code_task"}:
                    self._notify(event.get("text", ""), "Джарвис")
                elif event["type"] == "approval_request":
                    self._notify(f"Нужно подтверждение: {event.get('title', '')}", "Джарвис")
        finally:
            self.ctx.bus.unsubscribe(queue)

    def _notify(self, text, title):
        try:
            self.icon.notify(text, title)
        except Exception:
            _LOG.exception("Не удалось показать уведомление")

    def start(self):
        if self._thread is not None and self._thread.is_alive():
            return
        self._queue = self.ctx.bus.subscribe()
        self._task = self.ctx.loop.create_task(self._events())
        self._thread = threading.Thread(target=self.icon.run, name="jarvis-tray", daemon=True)
        self._thread.start()

    def stop(self):
        if self._task is not None:
            # stop() зовут из рабочего потока, отменять задачу можно только из её loop.
            self.ctx.loop.call_soon_threadsafe(self._task.cancel)
            self._task = None
        self.icon.stop()
        if self._thread is not None and self._thread is not threading.current_thread():
            self._thread.join(timeout=3)
        self._thread = None
