from __future__ import annotations

import time


class WakeWordDetector:
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

    def process(self, block, threshold: float | None = None) -> bool:
        self.load()
        now = time.monotonic()
        if now < self._cooldown_until:
            return False
        scores = self._model.predict(block)
        self._max_score = max(self._max_score, *(float(score) for score in scores.values()))
        limit = self.threshold if threshold is None else threshold
        if any(float(score) > limit for score in scores.values()):
            self._model.reset()
            self._cooldown_until = now + 1.5
            return True
        return False

    def reset(self) -> None:
        self._cooldown_until = 0.0
        if self._model is not None:
            self._model.reset()
