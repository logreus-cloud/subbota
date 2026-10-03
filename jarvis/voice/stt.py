from __future__ import annotations

import logging
import re
import warnings

from jarvis.config import VoiceConfig

_LOG = logging.getLogger(__name__)
_HALLUCINATIONS = re.compile(
    r"(?:продолжение следует|спасибо за просмотр)[.!?…\s]*|субтитры сделал[^\n.!?…]*[.!?…\s]*",
    re.IGNORECASE,
)


class Transcriber:
    def __init__(self, cfg: VoiceConfig) -> None:
        self.cfg = cfg
        self._model = None

    def load(self) -> None:
        if self._model is not None:
            return
        from jarvis.cuda import add_nvidia_dll_dirs
        add_nvidia_dll_dirs()
        from faster_whisper import WhisperModel
        try:
            model = WhisperModel(
                self.cfg.whisper_model, device=self.cfg.whisper_device,
                compute_type=self.cfg.whisper_compute_type,
            )
            # Нехватка cuBLAS/cuDNN часто вылезает только на первом распознавании,
            # поэтому прогреваем модель сразу и при ошибке уходим на CPU.
            import numpy as np
            segments, _ = model.transcribe(np.zeros(16000, dtype=np.float32), language=self.cfg.language)
            list(segments)
            self._model = model
        except Exception as exc:
            if self.cfg.whisper_device == "cpu":
                raise
            warnings.warn("CUDA недоступна; распознавание переключено на CPU", RuntimeWarning, stacklevel=2)
            _LOG.warning("Не удалось загрузить Whisper на CUDA: %s", exc)
            self._model = WhisperModel(self.cfg.whisper_model, device="cpu", compute_type="int8")

    def transcribe(self, audio_int16) -> str:
        import numpy as np
        self.load()
        if audio_int16.size == 0:
            return ""
        samples = audio_int16.astype(np.float32) / 32768
        segments, _ = self._model.transcribe(
            samples,
            language=self.cfg.language,
            beam_size=5,
            vad_filter=True,
            initial_prompt="Джарвис, ",
        )
        text = " ".join(segment.text for segment in segments).strip()
        text = _HALLUCINATIONS.sub("", text).strip(" \t\n.!?…")
        return text if len(text) >= 2 else ""
