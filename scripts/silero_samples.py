"""Образцы голосов Silero: uv run python scripts/silero_samples.py [спикер ...]."""
from __future__ import annotations

import sys
import time
from pathlib import Path

import sounddevice as sd
import torch

ROOT = Path(__file__).resolve().parent.parent
PHRASE = "Добрый вечер, сэр. Все системы в норме. Чем могу помочь?"


def main() -> None:
    torch.set_num_threads(4)
    importer = torch.package.PackageImporter(str(ROOT / "models" / "silero" / "v5_1_ru.pt"))
    model = importer.load_pickle("tts_models", "model")
    model.to(torch.device("cpu"))
    print("Спикеры:", model.speakers)
    speakers = sys.argv[1:] or [s for s in model.speakers if s != "random"]
    for number, speaker in enumerate(speakers, 1):
        started = time.perf_counter()
        audio = model.apply_tts(text=f"Голос {number}. {PHRASE}", speaker=speaker, sample_rate=48000)
        print(f"{number}. {speaker} — синтез {time.perf_counter() - started:.2f} с")
        sd.play(audio.numpy(), 48000)
        sd.wait()


if __name__ == "__main__":
    main()
