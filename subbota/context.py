from __future__ import annotations

import asyncio
import threading
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from subbota.config import Config
from subbota.db import Database
from subbota.events import EventBus

if TYPE_CHECKING:
    from subbota.agent import Brain
    from subbota.permissions import PermissionGate
    from subbota.scheduler import ReminderScheduler
    from subbota.tools.code import CodeTaskManager
    from subbota.voice.loop import VoiceLoop


@dataclass
class AppContext:
    cfg: Config
    bus: EventBus
    db: Database
    gate: PermissionGate
    loop: asyncio.AbstractEventLoop
    brain: Brain | None = None
    scheduler: ReminderScheduler | None = None
    voice: VoiceLoop | None = None
    code_tasks: CodeTaskManager | None = None
    state: dict = field(default_factory=lambda: {"status": "idle", "mic_muted": False})
    _state_lock: threading.Lock = field(default_factory=threading.Lock, init=False, repr=False)

    def set_status(self, status: str) -> None:
        """Обновляет состояние и рассылает его подписчикам."""
        try:
            in_loop = asyncio.get_running_loop() is self.loop
        except RuntimeError:
            in_loop = False
        if not in_loop:
            self.loop.call_soon_threadsafe(self.set_status, status)
            return
        with self._state_lock:
            self.state["status"] = status
            payload = {
                "status": self.state["status"],
                "mic_muted": self.state["mic_muted"],
            }
        self.bus.publish("status", **payload)

    def set_mic_muted(self, muted: bool) -> None:
        """Переключает микрофон и рассылает состояние."""
        try:
            in_loop = asyncio.get_running_loop() is self.loop
        except RuntimeError:
            in_loop = False
        if not in_loop:
            self.loop.call_soon_threadsafe(self.set_mic_muted, muted)
            return
        with self._state_lock:
            self.state["mic_muted"] = muted
            payload = {
                "status": self.state["status"],
                "mic_muted": self.state["mic_muted"],
            }
        if self.voice is not None:
            self.voice.set_muted(muted)
        self.bus.publish("status", **payload)

    async def announce(self, text: str, kind: str = "info") -> None:
        """Показывает и озвучивает объявление."""
        self.bus.publish("announcement", text=text, kind=kind)
        if self.voice is not None:
            self.voice.say(text)
