from __future__ import annotations

import json
import logging
import time
from typing import Literal

from subbota.config import VoiceConfig

_LOG = logging.getLogger(__name__)
# Что услышал детектор: обращение по имени или стоп-слово (перебивание).
WakeEvent = Literal["wake", "stop"] | None
_STOP_WORDS = ("стоп", "хватит", "замолчи")


class WakeWordDetector:
    """«Hey Jarvis» через openWakeWord: стоп-слов не умеет."""

    def __init__(self, threshold: float) -> None:
        self.threshold = threshold
        self._model = None
        self._cooldown_until = 0.0
        self._max_score = 0.0

    def take_max_score(self) -> float:
        """Максимальная оценка с прошлого вызова (для диагностики)."""
        value, self._max_score = self._max_score, 0.0
        return value

    def load(self) -> None:
        if self._model is not None:
            return
        import openwakeword
        from openwakeword.model import Model
        path = openwakeword.models["hey_jarvis"]["model_path"]
        self._model = Model(wakeword_model_paths=[path])

    def process(self, block, threshold: float | None = None) -> WakeEvent:
        self.load()
        now = time.monotonic()
        if now < self._cooldown_until:
            return None
        scores = self._model.predict(block)
        self._max_score = max(self._max_score, *(float(score) for score in scores.values()))
        limit = self.threshold if threshold is None else threshold
        if any(float(score) > limit for score in scores.values()):
            self._model.reset()
            self._cooldown_until = now + 1.5
            return "wake"
        return None

    def reset(self) -> None:
        self._cooldown_until = 0.0
        if self._model is not None:
            self._model.reset()


class VoskKeywordSpotter:
    """Обращение по имени («Суббота») и стоп-слова через Vosk с узкой грамматикой.

    Грамматика из нескольких слов плюс [unk]: всё остальное распознаётся как
    «неизвестное», поэтому Vosk почти не грузит CPU и не путает обычную речь
    с именем. Срабатываем по частичному результату, чтобы не ждать паузы.
    """

    def __init__(self, cfg: VoiceConfig) -> None:
        self.cfg = cfg
        self.word = cfg.wake_word.casefold()
        self._model = None
        self._rec = None
        self._cooldown_until = 0.0
        self._last_heard = ""

    def take_max_score(self) -> float:
        return 0.0

    def load(self) -> None:
        if self._rec is not None:
            return
        import vosk
        vosk.SetLogLevel(-1)
        self._model = vosk.Model(str(self.cfg.vosk_model))
        # Без грамматики: с узким словарём Vosk подгоняет любую речь под имя
        # («в субботу», «сегодня» → «суббота»). Полная модель выдаёт настоящие
        # слова, и имя срабатывает только как отдельное слово.
        self._rec = vosk.KaldiRecognizer(self._model, 16000)

    def process(self, block, threshold: float | None = None) -> WakeEvent:
        self.load()
        final = self._rec.AcceptWaveform(block.tobytes())
        raw = self._rec.Result() if final else self._rec.PartialResult()
        text = json.loads(raw).get("text" if final else "partial", "")
        if not text or time.monotonic() < self._cooldown_until:
            return None
        words = text.split()
        event: WakeEvent = None
        if self.word in words:
            event = "wake"
        elif any(word in _STOP_WORDS for word in words):
            event = "stop"
        if event is not None:
            _LOG.info("Голос: Vosk услышал %r", text)
            self._rec.Reset()
            self._cooldown_until = time.monotonic() + 1.0
        return event

    def reset(self) -> None:
        self._cooldown_until = 0.0
        if self._rec is not None:
            self._rec.Reset()


def make_detector(cfg: VoiceConfig) -> WakeWordDetector | VoskKeywordSpotter:
    if cfg.wake_engine == "vosk":
        return VoskKeywordSpotter(cfg)
    if cfg.wake_engine == "openwakeword":
        return WakeWordDetector(cfg.wake_threshold)
    raise ValueError(f"Неизвестный движок обращения: {cfg.wake_engine!r}")
