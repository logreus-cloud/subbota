"""Проигрывает образцы голосов Piper: uv run python scripts/voice_samples.py [имя ...]."""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import sounddevice as sd
from piper import PiperVoice, SynthesisConfig

ROOT = Path(__file__).resolve().parent.parent
PHRASE = "Добрый вечер, сэр. Все системы в норме. Чем могу помочь?"


def play(name: str, label: str, length_scale: float = 1.0) -> None:
    voice = PiperVoice.load(ROOT / "models" / "piper" / f"{name}.onnx")
    config = SynthesisConfig(length_scale=length_scale)
    audio = np.concatenate([chunk.audio_int16_array for chunk in voice.synthesize(f"{label}. {PHRASE}", config)])
    sd.play(audio, voice.config.sample_rate)
    sd.wait()


def main() -> None:
    names = sys.argv[1:] or ["ru_RU-dmitri-medium", "ru_RU-ruslan-medium", "ru_RU-irina-medium", "ru_RU-denis-medium"]
    for number, name in enumerate(names, 1):
        print(f"{number}. {name}")
        play(name, f"Голос {number}")


if __name__ == "__main__":
    main()
