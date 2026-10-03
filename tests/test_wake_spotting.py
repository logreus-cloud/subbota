"""Обращение «Суббота» на синтезированной речи: имя ловится, похожие слова — нет.

Медленный тест (грузит Silero и Vosk); пропускается, если моделей нет.
Запуск только его: uv run pytest -m slow
"""
import numpy as np
import pytest

from subbota.config import load_config

cfg = load_config().voice
pytestmark = [
    pytest.mark.slow,
    pytest.mark.skipif(
        not (cfg.silero_model.exists() and cfg.vosk_model.exists()),
        reason="нет моделей Silero/Vosk",
    ),
]

CASES = [
    ("Суббота, какая сейчас громкость?", ["wake"]),
    ("Слушай, Суббота, открой браузер.", ["wake"]),
    ("В субботу пойдём гулять в парк.", []),
    ("Субботний вечер был спокойным.", []),
    ("Какая сегодня погода? Расскажи новости.", []),
    ("Стоп.", ["stop"]),
    ("Хватит.", ["stop"]),
]


@pytest.fixture(scope="module")
def silero():
    import torch
    importer = torch.package.PackageImporter(str(cfg.silero_model))
    return importer.load_pickle("tts_models", "model")


def _events(model, speaker, phrase):
    from scipy.signal import resample_poly

    from subbota.voice.wake import VoskKeywordSpotter
    audio = model.apply_tts(text=phrase, speaker=speaker, sample_rate=48000).numpy()
    pcm = (np.clip(resample_poly(audio, 1, 3), -1, 1) * 16000).astype(np.int16)
    pcm = np.concatenate([np.zeros(8000, np.int16), pcm, np.zeros(16000, np.int16)])
    spotter = VoskKeywordSpotter(cfg)
    found = []
    for start in range(0, len(pcm) - 1279, 1280):
        event = spotter.process(pcm[start:start + 1280])
        if event:
            found.append(event)
    return found


@pytest.mark.parametrize("speaker", ["aidar", "xenia"])
@pytest.mark.parametrize("phrase,expected", CASES)
def test_wake_spotting(silero, speaker, phrase, expected):
    assert _events(silero, speaker, phrase) == expected
