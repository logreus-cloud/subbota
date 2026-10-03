"""Сравнение моделей Whisper на синтезированной речи: uv run python scripts/stt_bench.py small large-v3-turbo"""
from __future__ import annotations

import re
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np
import torch
from scipy.signal import resample_poly

from subbota.config import load_config
from subbota.cuda import add_nvidia_dll_dirs

HOT = None
PHRASES = [
    "Суббота, какая сейчас громкость и сколько свободно оперативной памяти?",
    "Открой телеграм и найди переписку с Артёмом.",
    "Напомни мне завтра в девять утра позвонить в банк.",
    "Создай в проекте GenshinFlex новую страницу с гайдом на Фурину.",
    "Сделай громкость на сорок процентов и включи следующий трек.",
    "Какая погода будет в выходные в Казани?",
    "Запусти задачу в notabene: поправь поиск по заметкам с опечатками.",
    "Выключи компьютер через полчаса.",
]


def words(text: str) -> list[str]:
    return re.findall(r"[а-яёa-z0-9]+", text.casefold().replace("ё", "е"))


def wer(ref: str, hyp: str) -> float:
    r, h = words(ref), words(hyp)
    d = np.zeros((len(r) + 1, len(h) + 1), dtype=int)
    d[:, 0] = range(len(r) + 1)
    d[0, :] = range(len(h) + 1)
    for i in range(1, len(r) + 1):
        for j in range(1, len(h) + 1):
            d[i, j] = min(d[i - 1, j] + 1, d[i, j - 1] + 1, d[i - 1, j - 1] + (r[i - 1] != h[j - 1]))
    return d[len(r), len(h)] / max(1, len(r))


def main() -> None:
    global HOT
    cfg = load_config().voice
    if "--hot" in sys.argv:
        sys.argv.remove("--hot")
        roots = load_config().code.workspace_roots
        names = sorted({d.name for root in roots for d in root.iterdir() if d.is_dir() and not d.name.startswith(".")})
        HOT = "Суббота " + " ".join(names)
        print("hotwords:", HOT)
    importer = torch.package.PackageImporter(str(cfg.silero_model))
    tts = importer.load_pickle("tts_models", "model")
    clips = []
    for index, phrase in enumerate(PHRASES):
        speaker = ("aidar", "xenia", "eugene", "baya")[index % 4]
        audio = tts.apply_tts(text=phrase, speaker=speaker, sample_rate=48000).numpy()
        noisy = resample_poly(audio, 1, 3) + np.random.default_rng(index).normal(0, 0.01, len(audio) // 3 + 1)[: len(audio) // 3 + (len(audio) % 3 > 0)]
        clips.append(noisy.astype(np.float32))
    add_nvidia_dll_dirs()
    from faster_whisper import WhisperModel
    for name in sys.argv[1:] or ["small", "large-v3-turbo"]:
        started = time.perf_counter()
        model = WhisperModel(name, device="cuda", compute_type="float16")
        load_s = time.perf_counter() - started
        total, spent = 0.0, 0.0
        for phrase, clip in zip(PHRASES, clips):
            started = time.perf_counter()
            segments, _ = model.transcribe(clip, language="ru", beam_size=5, vad_filter=True, initial_prompt="Суббота, ", hotwords=HOT)
            text = " ".join(s.text for s in segments).strip()
            spent += time.perf_counter() - started
            total += wer(phrase, text)
            print(f"  [{name}] {text}")
        print(f"{name}: WER {total / len(PHRASES):.1%}, распознавание {spent / len(PHRASES):.2f} с/фразу, загрузка {load_s:.1f} с")
        del model


if __name__ == "__main__":
    main()
