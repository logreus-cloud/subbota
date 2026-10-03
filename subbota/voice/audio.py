from __future__ import annotations

import logging
import queue
import threading
import time

import numpy as np

_LOG = logging.getLogger(__name__)


class MicStream:
    def __init__(self, device: int | str | None, block: int = 1280, rate: int = 16000) -> None:
        self.device = device
        self.block = block
        self.rate = rate
        self._blocks: queue.Queue[np.ndarray] = queue.Queue(maxsize=200)
        self._stream = None
        self._lock = threading.Lock()
        self._closed = False
        self._last_restart = 0.0

    def _callback(self, data, frames, timing, status) -> None:
        if status:
            _LOG.warning("Состояние микрофона: %s", status)
        block = data[:, 0].copy()
        try:
            self._blocks.put_nowait(block)
        except queue.Full:
            try:
                self._blocks.get_nowait()
            except queue.Empty:
                pass
            self._blocks.put_nowait(block)

    def start(self) -> None:
        import sounddevice as sd

        with self._lock:
            if self._closed:
                raise RuntimeError("Микрофон закрыт")
            if self._stream is not None and self._stream.active:
                return
            self.drain()
            stream = sd.InputStream(
                device=self.device,
                samplerate=self.rate,
                dtype="int16",
                channels=1,
                blocksize=self.block,
                callback=self._callback,
            )
            try:
                stream.start()
            except Exception:
                stream.close()
                raise
            self._stream = stream

    def stop(self) -> None:
        with self._lock:
            self._closed = True
            stream = self._stream
            self._stream = None
        if stream is not None:
            try:
                stream.stop()
            except Exception:
                _LOG.exception("Не удалось остановить микрофон")
            try:
                stream.close()
            except Exception:
                _LOG.exception("Не удалось закрыть микрофон")
        self.drain()

    def read(self, timeout: float) -> np.ndarray | None:
        stream = self._stream
        if stream is None or not stream.active:
            return None
        try:
            return self._blocks.get(timeout=timeout)
        except queue.Empty:
            return None

    def drain(self) -> None:
        while True:
            try:
                self._blocks.get_nowait()
            except queue.Empty:
                return

    def restart(self) -> bool:
        with self._lock:
            if self._closed:
                return False
            if self._stream is not None and self._stream.active:
                return True
            now = time.monotonic()
            if now - self._last_restart < 3:
                return False
            self._last_restart = now
            stream = self._stream
            self._stream = None
        if stream is not None:
            try:
                stream.close()
            except Exception:
                _LOG.exception("Не удалось закрыть отключённый микрофон")
        self.drain()
        try:
            self.start()
        except Exception:
            _LOG.exception("Не удалось переподключить микрофон")
            return False
        return True


def rms(block: np.ndarray) -> float:
    if block.size == 0:
        return 0.0
    values = block.astype(np.float32)
    return float(np.sqrt(np.mean(values * values)) / 32768)
