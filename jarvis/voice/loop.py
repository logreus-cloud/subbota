from __future__ import annotations

import asyncio
import logging
import queue
import re
import threading
import time
from collections import deque
from concurrent.futures import Future, InvalidStateError
from dataclasses import dataclass
from typing import TYPE_CHECKING

import numpy as np

from jarvis.voice.audio import MicStream, rms
from jarvis.voice.stt import Transcriber
from jarvis.voice.tts import Speaker
from jarvis.voice.wake import make_detector

if TYPE_CHECKING:
    from jarvis.context import AppContext

_LOG = logging.getLogger(__name__)
_ECHO_GUARD_S = 0.35
_MIN_SPEECH_THRESHOLD = 0.01
_MAX_SPEECH_THRESHOLD = 0.03
_STOP = re.compile(r"^(?:джарвис\W*)?(?:стоп|хватит|замолчи|отмена)\b")
_NO = {
    "нет", "отмена", "отменить", "отмени", "запрещаю", "нельзя", "не",
    "стоп", "стой", "погоди", "подожди", "потом", "позже", "хватит",
}
# Согласие засчитывается, только если вся фраза из этих слов: «да, но подожди»,
# «давай не будем» или «да что ты делаешь» не должны разрешать действие.
_YES = {
    "да", "разрешаю", "давай", "ок", "окей", "выполняй", "выполни", "конечно",
    "подтверждаю", "можно", "ага", "угу", "хорошо", "ладно", "пожалуйста",
    "джарвис", "сэр",
}
_YES_CORE = _YES - {"пожалуйста", "джарвис", "сэр"}


def parse_yes_no(text: str) -> bool | None:
    words = re.findall(r"[а-яёa-z]+", text.casefold())
    if not words:
        return None
    if any(word in _NO for word in words):
        # «нет», «не надо», «не разрешаю» — отказ; отказ вперемешку с другим
        # согласием («да нет», «давай не будем») тоже не разрешает, но и не
        # считается чётким ответом.
        free_yes = [
            word for index, word in enumerate(words)
            if word in _YES_CORE and not (index > 0 and words[index - 1] == "не")
        ]
        return None if free_yes else False
    if all(word in _YES for word in words) and any(word in _YES_CORE for word in words):
        return True
    return None


@dataclass
class _Speech:
    id: int
    text: str
    follow_up: bool = False
    generation: int = 0
    interrupted: bool = False


@dataclass
class _Confirmation:
    future: Future[bool | None]
    question: _Speech
    timeout: float
    deadline: float = 0.0


def _safe_set(fut: Future[bool | None], value: bool | None) -> None:
    try:
        fut.set_result(value)
    except InvalidStateError:
        pass


class VoiceLoop:
    def __init__(self, ctx: AppContext) -> None:
        self.ctx = ctx
        self.cfg = ctx.cfg.voice
        self.mic = MicStream(self.cfg.input_device)
        self.wake = make_detector(self.cfg)
        self.transcriber = Transcriber(self.cfg)
        self.speaker = Speaker(self.cfg)
        self._commands: queue.Queue[tuple] = queue.Queue()
        self._talker_jobs: queue.Queue[tuple[int, str, int] | None] = queue.Queue()
        self._stt_jobs: queue.Queue[tuple[int, np.ndarray] | None] = queue.Queue()
        self._lifecycle = threading.Lock()
        self._closed = threading.Event()
        self._thread: threading.Thread | None = None
        self._talker: threading.Thread | None = None
        self._stt_thread: threading.Thread | None = None
        self._stopping = False
        self._muted = False
        self._state = "IDLE"
        self._reported_status = ""
        self._speeches: deque[_Speech] = deque()
        self._confirms: deque[tuple[str, float, Future[bool | None]]] = deque()
        self._active_confirm: _Confirmation | None = None
        self._active_speech: _Speech | None = None
        self._next_speech_id = 0
        self._next_record_id = 0
        self._record_id: int | None = None
        self._record_confirm: _Confirmation | None = None
        self._recording_reserved = False
        self._pending_wake = False
        self._reply_generation = 0
        self._pending: dict[Future, int] = {}
        self._noise: deque[float] = deque(maxlen=62)  # ~5 с в IDLE
        self._recorded: list[np.ndarray] = []
        self._last_voice = 0.0
        self._speech_s = 0.0
        self._heard = False
        self._record_limit = 0.0
        self._no_speech_limit = 0.0
        self._follow_up_until = 0.0

    def start(self) -> None:
        with self._lifecycle:
            if self._thread is not None or self._closed.is_set():
                return
            self._thread = threading.Thread(target=self.run, name="jarvis-voice", daemon=True)
            self._thread.start()

    def _enqueue(self, command: tuple) -> bool:
        with self._lifecycle:
            if self._closed.is_set():
                return False
            self._commands.put(command)
            return True

    def say(self, text: str) -> None:
        if text.strip():
            self._enqueue(("say", text))

    def confirm(self, question: str, timeout: float = 20.0) -> Future[bool | None]:
        result: Future[bool | None] = Future()
        if not self._enqueue(("confirm", question, timeout, result)):
            _safe_set(result, None)
            return result
        # Если подтверждение решили в панели, гейт отменяет future: автомат
        # должен перестать задавать вопрос и слушать ответ.
        result.add_done_callback(lambda done: self._enqueue(("confirm_done", done)))
        return result

    def set_muted(self, muted: bool) -> None:
        self._enqueue(("mute", muted))

    def stop_speaking(self) -> None:
        self._enqueue(("stop_speaking",))

    def stop(self) -> None:
        with self._lifecycle:
            if self._closed.is_set():
                return
            self._commands.put(("stop",))
            if self._thread is None:
                self._thread = threading.Thread(target=self.run, name="jarvis-voice", daemon=True)
                self._thread.start()
            thread = self._thread
        if thread is not threading.current_thread():
            self._closed.wait()
            thread.join()

    def run(self) -> None:
        try:
            self._drain_commands()
            if not self._stopping:
                try:
                    self.transcriber.load()
                    self.speaker.load()
                    self.wake.load()
                except Exception as exc:
                    _LOG.exception("Не удалось загрузить голосовые модели")
                    self.ctx.bus.publish("error", message=str(exc))
                    self._stopping = True
            if not self._stopping:
                self._talker = threading.Thread(target=self._talk, name="jarvis-speaker", daemon=True)
                self._stt_thread = threading.Thread(
                    target=self._transcribe, name="jarvis-transcriber", daemon=True,
                )
                self._talker.start()
                self._stt_thread.start()
                try:
                    self.mic.start()
                    _LOG.info("Голос: микрофон %r, порог wake %.2f", self.cfg.input_device, self.cfg.wake_threshold)
                except Exception:
                    _LOG.exception("Не удалось открыть микрофон")
                while not self._stopping:
                    try:
                        self._step()
                    except Exception as exc:
                        _LOG.exception("Ошибка голосового цикла")
                        self.ctx.bus.publish("error", message=str(exc))
                        self._cancel_recording()
                        self.mic.restart()
                        time.sleep(0.05)
        finally:
            with self._lifecycle:
                self._closed.set()
            self._stopping = True
            self.speaker.stop()
            self._talker_jobs.put(None)
            self._stt_jobs.put(None)
            if self._talker is not None:
                self._talker.join(timeout=5)
            if self._stt_thread is not None:
                self._stt_thread.join(timeout=3)
            self.speaker.close()
            self.mic.stop()
            self._drain_commands()
            self._cancel_confirms()
            try:
                self.ctx.set_status("idle")
            except RuntimeError:
                pass  # главный loop уже закрыт

    def _talk(self) -> None:
        while True:
            job = self._talker_jobs.get()
            if job is None:
                return
            speech_id, text, generation = job
            try:
                self.speaker.speak(text, generation=generation)
            except Exception as exc:
                _LOG.exception("Ошибка озвучки")
                self._commands.put(("speech_done", speech_id, exc))
            else:
                self._commands.put(("speech_done", speech_id, None))

    def _transcribe(self) -> None:
        while True:
            job = self._stt_jobs.get()
            if job is None:
                return
            record_id, audio = job
            try:
                text = self.transcriber.transcribe(audio)
            except Exception as exc:
                self._commands.put(("transcribed", record_id, "", exc))
            else:
                self._commands.put(("transcribed", record_id, text, None))

    def _step(self) -> None:
        self._drain_commands()
        if self._stopping:
            return
        block = self.mic.read(0.05)
        if block is None:
            if not self.mic.restart():
                time.sleep(0.05)
        else:
            self._heartbeat(block)
            self._process_audio(block)
        self._check_deadlines()
        self._maybe_start_confirm()
        self._dispatch_speech()
        self._refresh_status()

    def _heartbeat(self, block: np.ndarray) -> None:
        # Раз в 10 с пишем в лог, что цикл жив и что он слышит.
        level = rms(block)
        self._hb_rms = max(getattr(self, "_hb_rms", 0.0), level)
        self._hb_blocks = getattr(self, "_hb_blocks", 0) + 1
        now = time.monotonic()
        if now < getattr(self, "_hb_next", 0.0):
            return
        if getattr(self, "_hb_next", 0.0):
            _LOG.info(
                "Голос: state=%s speech=%s confirm=%s muted=%s блоков=%d rms_max=%.4f wake_max=%.3f",
                self._state, self._active_speech.id if self._active_speech else None,
                self._active_confirm is not None, self._muted, self._hb_blocks,
                self._hb_rms, self.wake.take_max_score(),
            )
        self._hb_next = now + 10
        self._hb_rms = 0.0
        self._hb_blocks = 0

    def _drain_commands(self) -> None:
        while True:
            try:
                command = self._commands.get_nowait()
            except queue.Empty:
                return
            try:
                self._handle_command(command)
            except Exception as exc:
                _LOG.exception("Ошибка голосовой команды")
                self.ctx.bus.publish("error", message=str(exc))

    def _handle_command(self, command: tuple) -> None:
        kind, *args = command
        if kind == "stop":
            self._stop_owner()
        elif kind == "mute":
            self._muted = args[0]
            if self._muted:
                if self._state in {"RECORDING", "TRANSCRIBING"}:
                    self._cancel_recording()
                if self._active_confirm is not None:
                    self._resolve_confirm(self._active_confirm, None)
                if self._state == "FOLLOW_UP":
                    self._state = "IDLE"
        elif kind == "say" and not self._stopping:
            self._speeches.append(self._new_speech(args[0]))
        elif kind == "confirm":
            if self._stopping or self._muted:
                _safe_set(args[2], None)
            else:
                self._confirms.append((args[0], args[1], args[2]))
        elif kind == "stop_speaking":
            self._barge_speech()
            if self._active_confirm is not None:
                self._resolve_confirm(self._active_confirm, None)
        elif kind == "confirm_done":
            active = self._active_confirm
            if active is not None and active.future is args[0]:
                self._resolve_confirm(active, None)
        elif kind == "speech_done":
            self._on_speech_done(*args)
        elif kind == "transcribed":
            self._on_transcribed(*args)
        elif kind == "brain_done":
            self._on_brain_done(args[0])

    def _stop_owner(self) -> None:
        if self._stopping:
            return
        self._stopping = True
        self._barge_speech()
        self._cancel_confirms()
        self._record_id = None
        self._recorded = []
        self._state = "IDLE"
        self._speeches.clear()

    def _new_speech(self, text: str, follow_up: bool = False) -> _Speech:
        self._next_speech_id += 1
        return _Speech(self._next_speech_id, text, follow_up, self._reply_generation)

    def _refresh_status(self) -> None:
        if self._active_speech is not None:
            status = "speaking"
        else:
            status = {
                "RECORDING": "recording", "TRANSCRIBING": "transcribing",
                "FOLLOW_UP": "listening",
            }.get(self._state, "idle")
            if self._state == "IDLE" and self._reply_generation in self._pending.values():
                status = "thinking"
        if status != self._reported_status:
            self._reported_status = status
            self.ctx.set_status(status)

    def _speech_threshold(self) -> float:
        # Пол шума — нижний перцентиль, а не среднее: иначе в него попадает само
        # «Hey Jarvis», порог взлетает выше обычной речи и запись сбрасывается.
        floor = float(np.percentile(self._noise, 20)) if self._noise else 0.0
        return min(_MAX_SPEECH_THRESHOLD, max(_MIN_SPEECH_THRESHOLD, floor * 3))

    def _process_audio(self, block: np.ndarray) -> None:
        if self._muted:
            return
        if self._active_speech is not None:
            # Перебивание: имя — замолчать и слушать новый вопрос, стоп-слово — просто замолчать.
            event = self.wake.process(block, threshold=min(1.0, self.cfg.wake_threshold + 0.2))
            if event == "wake":
                self._barge_in()
            elif event == "stop":
                self._stop_word()
            return
        if self._recording_reserved or self._active_confirm is not None and self._state != "RECORDING":
            return
        if self._state == "RECORDING":
            self._record(block)
        elif self._state == "FOLLOW_UP":
            if rms(block) > self._speech_threshold():
                self._begin_recording(first=block)
        elif self._state == "IDLE":
            self._noise.append(rms(block))
            if self.wake.process(block) == "wake":
                _LOG.info("Голос: обращение")
                # Вопрос часто идёт сразу за именем: блок с именем оставляем в записи.
                self._begin_recording(first=block, beep=True)

    def _begin_recording(
        self, first: np.ndarray | None = None, beep: bool = False,
        confirm: _Confirmation | None = None,
    ) -> None:
        if self._muted or self._stopping or self._active_speech is not None or (
            confirm is not None and self._active_confirm is not confirm
        ):
            return
        self._recording_reserved = True
        if beep:
            # Буфер микрофона не сбрасываем: «Джарвис, какая громкость» говорят
            # на одном дыхании, и начало вопроса пришлось бы на сигнал.
            self.speaker.beep("wake")
        now = time.monotonic()
        self._next_record_id += 1
        self._record_id = self._next_record_id
        self._record_confirm = confirm
        self._recorded = []
        self._last_voice = now
        self._speech_s = 0.0
        self._heard = False
        self._record_limit = float("inf") if confirm is not None else now + self.cfg.max_record_s
        self._no_speech_limit = float("inf") if confirm is not None else now + 5
        self._state = "RECORDING"
        self._recording_reserved = False
        if first is not None:
            self._record(first)

    def _record(self, block: np.ndarray) -> None:
        now = time.monotonic()
        self._recorded.append(block)
        if rms(block) > self._speech_threshold():
            if self._record_confirm is not None and self._record_limit == float("inf"):
                self._record_limit = now + self.cfg.max_record_s
            self._speech_s += block.size / 16000
            self._last_voice = now
            if self._speech_s >= 0.3:
                self._heard = True
        if self._heard and now - self._last_voice >= self.cfg.silence_s:
            self._finish_recording()

    def _finish_recording(self) -> None:
        if self._state != "RECORDING" or self._record_id is None:
            return
        if not self._heard:
            _LOG.info("Голос: речь не услышана (порог %.4f), запись сброшена", self._speech_threshold())
            self._cancel_recording()
            return
        audio = np.concatenate(self._recorded)
        self._recorded = []
        self._state = "TRANSCRIBING"
        self._stt_jobs.put((self._record_id, audio))

    def _cancel_recording(self) -> None:
        active = self._record_confirm
        self._record_id = None
        self._record_confirm = None
        self._recorded = []
        self._recording_reserved = False
        self._state = "IDLE"
        if active is not None:
            self._resolve_confirm(active, None)

    def _on_transcribed(self, record_id: int, text: str, error: Exception | None) -> None:
        if record_id != self._record_id or self._state != "TRANSCRIBING" or self._muted or self._stopping:
            return
        active = self._record_confirm
        if active is not None and (
            active is not self._active_confirm or time.monotonic() >= active.deadline
        ):
            self._resolve_confirm(active, None)
            return
        self._record_id = None
        self._record_confirm = None
        self._state = "IDLE"
        _LOG.info("Голос: распознано %r%s", text, " (ответ на подтверждение)" if active else "")
        if error is not None:
            _LOG.error("Ошибка распознавания", exc_info=error)
            self.ctx.bus.publish("error", message=str(error))
            if active is not None:
                self._resolve_confirm(active, None)
            return
        if _STOP.match(text.casefold()):
            if active is not None:
                # «Стоп» в ответ на вопрос — это отказ в действии, а не прерывание хода.
                self._resolve_confirm(active, False)
                return
            self._interrupt()
            return
        if active is not None:
            answer = parse_yes_no(text)
            if answer is None and text and time.monotonic() < active.deadline - 1.0:
                self._begin_recording(confirm=active)
                return
            self._resolve_confirm(active, answer)
            return
        if text and self.ctx.brain is not None:
            future = asyncio.run_coroutine_threadsafe(
                self.ctx.brain.ask(text, "voice"), self.ctx.loop,
            )
            self._pending[future] = self._reply_generation
            future.add_done_callback(lambda done: self._commands.put(("brain_done", done)))

    def _check_deadlines(self) -> None:
        now = time.monotonic()
        active = self._active_confirm
        if active is not None and active.deadline and now >= active.deadline:
            self._resolve_confirm(active, None)
        if self._state == "RECORDING" and (
            now >= self._record_limit or not self._heard and now >= self._no_speech_limit
        ):
            self._finish_recording()
        elif self._state == "FOLLOW_UP" and now >= self._follow_up_until:
            self._state = "IDLE"

    def _interrupt(self) -> None:
        self._reply_generation += 1
        if self.ctx.brain is not None:
            asyncio.run_coroutine_threadsafe(self.ctx.brain.interrupt(), self.ctx.loop)
        self._barge_speech()

    def _barge_speech(self) -> None:
        for job in self._speeches:
            if job.follow_up:
                job.interrupted = True
        if self._active_speech is not None:
            self._active_speech.interrupted = True
        self.speaker.stop()

    def _stop_word(self) -> None:
        _LOG.info("Голос: стоп-слово во время речи")
        if self._active_confirm is not None:
            self._resolve_confirm(self._active_confirm, False)
        self._interrupt()

    def _barge_in(self) -> None:
        self._barge_speech()
        if self._active_confirm is not None:
            self._resolve_confirm(self._active_confirm, None)
        if self._active_speech is not None:
            self._pending_wake = True
            self._recording_reserved = True
        else:
            self._begin_recording(beep=True)

    def _dispatch_speech(self) -> None:
        if self._active_speech is not None or self._recording_reserved or self._state in {
            "RECORDING", "TRANSCRIBING",
        }:
            return
        while self._speeches:
            job = self._speeches.popleft()
            if job.interrupted or job.follow_up and job.generation != self._reply_generation:
                continue
            if self._state == "FOLLOW_UP":
                self._state = "IDLE"
            self._active_speech = job
            if self._active_confirm is not None and self._active_confirm.question is job:
                self._recording_reserved = True
            _LOG.info("Голос: говорю #%d (%d симв.)", job.id, len(job.text))
            self._talker_jobs.put((job.id, job.text, self.speaker.generation))
            return

    def _on_speech_done(self, speech_id: int, error: Exception | None) -> None:
        job = self._active_speech
        if job is None or job.id != speech_id:
            return
        self._active_speech = None
        _LOG.info("Голос: реплика #%d завершена%s", speech_id, f" с ошибкой: {error}" if error else "")
        if error is not None:
            job.interrupted = True
            self.ctx.bus.publish("error", message=str(error))
        if self._pending_wake:
            self._pending_wake = False
            self._recording_reserved = False
            self._begin_recording(beep=True)
            return
        active = self._active_confirm
        if active is not None and active.question is job:
            if job.interrupted or self._muted or self._stopping:
                self._resolve_confirm(active, None)
            else:
                self._settle_after_speech()
                active.deadline = time.monotonic() + max(0.0, active.timeout)
                self._begin_recording(confirm=active)
        elif job.follow_up and not job.interrupted and not self._muted and (
            self._active_confirm is None and self._state == "IDLE"
        ):
            self._settle_after_speech()
            self._follow_up_until = time.monotonic() + self.cfg.follow_up_s
            if self.cfg.follow_up_s > 0:
                self._state = "FOLLOW_UP"

    def _settle_after_speech(self) -> None:
        # Хвост собственной речи и реверберация не должны попасть в запись.
        time.sleep(_ECHO_GUARD_S)
        self.mic.drain()

    def _on_brain_done(self, future: Future) -> None:
        generation = self._pending.pop(future, None)
        if generation is None:
            return
        try:
            result = future.result()
        except Exception as exc:
            _LOG.exception("Не удалось получить голосовой ответ")
            self.ctx.bus.publish("error", message=str(exc))
            return
        if generation != self._reply_generation or not result.text:
            return
        if result.is_error:
            if result.text not in {"Прервано", "Остановлено"}:
                self._speeches.append(self._new_speech("Не получилось, подробности в панели."))
            return
        self._speeches.append(self._new_speech(result.text, follow_up=True))

    def _maybe_start_confirm(self) -> None:
        if self._active_confirm is not None or self._state not in {"IDLE", "FOLLOW_UP"} or (
            self._active_speech is not None or self._recording_reserved
        ):
            return
        while self._confirms:
            question, timeout, future = self._confirms.popleft()
            if future.done():
                continue
            if self._muted or self._stopping:
                _safe_set(future, None)
                continue
            job = self._new_speech(question)
            self._active_confirm = _Confirmation(future, job, timeout)
            self._speeches.append(job)
            return

    def _resolve_confirm(self, active: _Confirmation, value: bool | None) -> None:
        if active is not self._active_confirm:
            return
        if active.deadline and time.monotonic() >= active.deadline or self._muted or self._stopping:
            value = None
        self._active_confirm = None
        active.question.interrupted = True
        self._recording_reserved = False
        if self._record_confirm is active:
            self._record_id = None
            self._record_confirm = None
            self._recorded = []
            if self._state in {"RECORDING", "TRANSCRIBING"}:
                self._state = "IDLE"
        if self._active_speech is active.question:
            self._active_speech.interrupted = True
            self.speaker.stop()
        _safe_set(active.future, value)

    def _cancel_confirms(self) -> None:
        if self._active_confirm is not None:
            self._resolve_confirm(self._active_confirm, None)
        while self._confirms:
            _, _, future = self._confirms.popleft()
            _safe_set(future, None)
