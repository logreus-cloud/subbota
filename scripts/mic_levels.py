"""Уровень записи и mute у микрофонов Windows: uv run python scripts/mic_levels.py"""
from __future__ import annotations

import comtypes
from pycaw.pycaw import AudioUtilities, EDataFlow, DEVICE_STATE, IAudioEndpointVolume
from comtypes import CLSCTX_ALL


def main() -> None:
    comtypes.CoInitialize()
    enumerator = AudioUtilities.GetDeviceEnumerator()
    collection = enumerator.EnumAudioEndpoints(EDataFlow.eCapture.value, DEVICE_STATE.ACTIVE.value)
    default = enumerator.GetDefaultAudioEndpoint(EDataFlow.eCapture.value, 1).GetId()
    for index in range(collection.GetCount()):
        device = collection.Item(index)
        info = AudioUtilities.CreateDevice(device)
        volume = device.Activate(IAudioEndpointVolume._iid_, CLSCTX_ALL, None).QueryInterface(IAudioEndpointVolume)
        mark = "*" if device.GetId() == default else " "
        print(f"{mark} {info.FriendlyName}: уровень {volume.GetMasterVolumeLevelScalar():.0%}, mute={bool(volume.GetMute())}")


if __name__ == "__main__":
    main()
