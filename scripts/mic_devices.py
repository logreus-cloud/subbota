"""Уровень сигнала на каждом входе: uv run python scripts/mic_devices.py."""
from __future__ import annotations

import numpy as np
import sounddevice as sd

apis = sd.query_hostapis()
default_in = sd.default.device[0]
for index, dev in enumerate(sd.query_devices()):
    if dev["max_input_channels"] < 1:
        continue
    api = apis[dev["hostapi"]]["name"]
    rate = int(dev["default_samplerate"])
    try:
        audio = sd.rec(int(rate * 1.0), samplerate=rate, channels=1, dtype="float32", device=index)
        sd.wait()
        level = float(np.sqrt(np.mean(np.square(audio))))
        peak = float(np.max(np.abs(audio)))
        result = f"rms {level:.5f} peak {peak:.5f}"
    except Exception as exc:
        result = f"ошибка: {type(exc).__name__}: {str(exc)[:60]}"
    mark = "*" if index == default_in else " "
    print(f"{mark}{index:3} [{api}] {dev['name'][:45]:45} {result}")
