from __future__ import annotations

import queue
import re
import threading
from dataclasses import dataclass, field

import numpy as np

from subbota.config import VoiceConfig

_SENTENCES = re.compile(r"(?<=[.!?…])\s+")


def clean_for_speech(text: str) -> str:
    text = re.sub(r"```[\s\S]*?```", " (код в панели) ", text)
    text = re.sub(r"`([^`]*)`", r"\1", text)
    text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text)
    text = re.sub(r"https?://[^\s)]+|www\.[^\s)]+", " ссылка ", text)
    text = re.sub(r"(?m)^\s*(?:[-*+]|\d+[.)])\s+", "", text)
    text = re.sub(r"[*_#>]", "", text)
    return " ".join(text.split())


class _PiperEngine:
    def __init__(self, cfg: VoiceConfig) -> None:
        from piper import PiperVoice
        self._voice = PiperVoice.load(str(cfg.piper_voice))
        self.rate = self._voice.config.sample_rate

    def synthesize(self, text: str, cancel: threading.Event):
        for chunk in self._voice.synthesize(text):
            if cancel.is_set():
                return
            yield chunk.audio_int16_array


class _SileroEngine:
    """Нейросетевые голоса Silero (PyTorch на CPU, ~0.15 с на фразу)."""

    rate = 48000

    def __init__(self, cfg: VoiceConfig) -> None:
        import torch
        torch.set_num_threads(4)
        importer = torch.package.PackageImporter(str(cfg.silero_model))
        self._model = importer.load_pickle("tts_models", "model")
        self._model.to(torch.device("cpu"))
        if cfg.silero_speaker not in self._model.speakers:
            raise ValueError(f"Нет голоса Silero {cfg.silero_speaker!r}; есть: {', '.join(self._model.speakers)}")
        self._speaker = cfg.silero_speaker

    def synthesize(self, text: str, cancel: threading.Event):
        from subbota.voice.text_norm import normalize_for_tts
        text = normalize_for_tts(text)
        # Silero выбрасывает символы вне алфавита; если слов не осталось — молчим.
        if not re.search(r"[а-яёА-ЯЁ]", text):
            return
        audio = self._model.apply_tts(text=text, speaker=self._speaker, sample_rate=self.rate)
        if not cancel.is_set():
            yield (audio.clamp(-1, 1) * 32767).numpy().astype(np.int16)


def _make_engine(cfg: VoiceConfig):
    if cfg.tts_engine == "silero":
        return _SileroEngine(cfg)
    if cfg.tts_engine == "piper":
        return _PiperEngine(cfg)
    raise ValueError(f"Неизвестный движок озвучки: {cfg.tts_engine!r}")


@dataclass
class _Task:
    text: str
    cancel: threading.Event
    done: threading.Event = field(default_factory=threading.Event)
    data: np.ndarray | None = None
    error: Exception | None = None


class Speaker:
    def __init__(self, cfg: VoiceConfig) -> None:
        self.cfg = cfg
        self._voice = None
        self._speaking = threading.Event()
        self._speak_lock = threading.Lock()
        self._state_lock = threading.Lock()
        self._output_lock = threading.Lock()
        self._generation = 0
        self._cancel = threading.Event()
        self._closed = False
        self._jobs: queue.Queue[_Task | None] = queue.Queue(maxsize=2)
        self._worker = threading.Thread(target=self._synthesize, name="subbota-synthesis", daemon=True)
        self._worker.start()

    def load(self) -> None:
        if self._voice is not None:
            return
        self._voice = _make_engine(self.cfg)

    @property
    def is_speaking(self) -> bool:
        return self._speaking.is_set()

    @property
    def generation(self) -> int:
        with self._state_lock:
            return self._generation

    def stop(self) -> None:
        with self._state_lock:
            self._generation += 1
            self._cancel.set()
            self._cancel = threading.Event()

    def close(self) -> None:
        with self._state_lock:
            if self._closed:
                return
            self._closed = True
            self._generation += 1
            self._cancel.set()
        while True:
            try:
                self._jobs.put(None, timeout=0.05)
                break
            except queue.Full:
                continue
        if self._worker is not threading.current_thread():
            self._worker.join()

    def _synthesize(self) -> None:
        while True:
            task = self._jobs.get()
            if task is None:
                return
            try:
                if task.cancel.is_set():
                    continue
                chunks = []
                for chunk in self._voice.synthesize(task.text, task.cancel):
                    if task.cancel.is_set():
                        break
                    chunks.append(chunk)
                if not task.cancel.is_set():
                    task.data = np.concatenate(chunks) if chunks else np.empty(0, dtype=np.int16)
            except Exception as exc:
                task.error = exc
            finally:
                task.done.set()

    def _submit(self, text: str, cancel: threading.Event) -> _Task | None:
        task = _Task(text, cancel)
        while not cancel.is_set():
            with self._state_lock:
                if self._closed:
                    return None
            try:
                self._jobs.put(task, timeout=0.05)
                return task
            except queue.Full:
                continue
        return None

    def _play(
        self, samples: np.ndarray, rate: int, cancel: threading.Event | None = None,
    ) -> None:
        import sounddevice as sd
        step = max(1, int(rate * 0.05))
        with self._output_lock:
            with sd.OutputStream(
                device=self.cfg.output_device, samplerate=rate, channels=1, dtype="int16",
            ) as stream:
                for offset in range(0, len(samples), step):
                    if cancel is not None and cancel.is_set():
                        break
                    stream.write(samples[offset:offset + step].reshape(-1, 1))

    def speak(self, text: str, generation: int | None = None) -> None:
        cleaned = clean_for_speech(text)
        if not cleaned:
            return
        sentences = _SENTENCES.split(cleaned)
        with self._speak_lock:
            self.load()
            with self._state_lock:
                if self._closed or generation is not None and generation != self._generation:
                    return
                cancel = self._cancel
                self._speaking.set()
            try:
                pending = self._submit(sentences[0], cancel)
                for index, sentence in enumerate(sentences):
                    if pending is None:
                        break
                    while not cancel.is_set() and not pending.done.wait(0.05):
                        pass
                    if cancel.is_set():
                        break
                    if pending.error is not None:
                        raise pending.error
                    data = pending.data
                    if index + 1 < len(sentences):
                        pending = self._submit(sentences[index + 1], cancel)
                    if data is not None and data.size:
                        self._play(data, self._voice.rate, cancel)
            finally:
                self._speaking.clear()

    def beep(self, kind: str = "wake") -> None:
        pitches = {"wake": 880, "done": 660, "error": 330}
        duration = {"wake": 0.12, "done": 0.10, "error": 0.15}[kind]
        rate = self._voice.rate if self._voice is not None else 16000
        times = np.arange(int(rate * duration)) / rate
        envelope = np.sin(np.linspace(0, np.pi, len(times))) ** 2
        tone = (np.sin(2 * np.pi * pitches[kind] * times) * envelope * 8192).astype(np.int16)
        self._play(tone, rate)
