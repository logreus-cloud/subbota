"""Диагностика микрофона и wake word: uv run python scripts/mic_probe.py [секунды] [устройство]."""
from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np
import sounddevice as sd

from jarvis.config import load_config
from jarvis.voice.audio import MicStream, rms
from jarvis.voice.wake import make_detector


def main() -> None:
    seconds = float(sys.argv[1]) if len(sys.argv) > 1 else 15
    cfg = load_config().voice
    device = sys.argv[2] if len(sys.argv) > 2 else cfg.input_device
    if isinstance(device, str) and device.isdigit():
        device = int(device)
    info = sd.query_devices(device, kind="input")
    print(f"Устройство: {info['name']}, частота по умолчанию {info['default_samplerate']:.0f} Гц")
    wake = make_detector(cfg)
    wake.load()
    mic = MicStream(device)
    mic.start()
    print(f"Движок {cfg.wake_engine}: говорите «{cfg.wake_word}», «стоп» и обычные фразы {seconds:.0f} с…")
    end = time.monotonic() + seconds
    window_rms, window_score, blocks = 0.0, 0.0, 0
    tick = time.monotonic() + 0.5
    while time.monotonic() < end:
        block = mic.read(timeout=0.5)
        if block is None:
            print("  нет данных с микрофона")
            continue
        blocks += 1
        window_rms = max(window_rms, rms(block))
        event = wake.process(block)
        if event:
            print(f"  >>> {event.upper()}")
        window_score = max(window_score, wake.take_max_score())
        if time.monotonic() >= tick:
            bar = "#" * int(min(window_score, 1) * 20)
            print(f"  rms {window_rms:.4f}  wake {window_score:.3f} {bar}")
            window_rms, window_score = 0.0, 0.0
            tick = time.monotonic() + 0.5
    mic.stop()
    print(f"Блоков: {blocks} (ожидалось ~{seconds * 16000 / 1280:.0f})")


if __name__ == "__main__":
    np.set_printoptions(precision=3)
    main()
