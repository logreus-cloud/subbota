"""Подготовка текста к озвучке: Silero не читает цифры и латиницу, поэтому
числа, время, проценты и единицы измерения переводим в слова."""
from __future__ import annotations

import re

from num2words import num2words

_UNITS = {
    "гб": "гигабайт", "мб": "мегабайт", "кб": "килобайт", "тб": "терабайт",
    "ггц": "гигагерц", "мгц": "мегагерц", "км": "километров", "кг": "килограмм",
    "мс": "миллисекунд", "мин": "минут", "°c": "градусов", "°": "градусов",
}
_LATIN = {
    "gpu": "джи пи ю", "cpu": "си пи ю", "ram": "оперативка", "usb": "ю эс би",
    "wi-fi": "вай фай", "wifi": "вай фай", "ok": "окей", "windows": "виндоус",
    "python": "питон", "chrome": "хром", "youtube": "ютуб", "github": "гитхаб",
}
_UNIT_RE = re.compile(r"(\d+(?:[.,]\d+)?)\s*(" + "|".join(re.escape(u) for u in sorted(_UNITS, key=len, reverse=True)) + r")\b", re.IGNORECASE)
_TIME_RE = re.compile(r"\b([01]?\d|2[0-3]):([0-5]\d)\b")
_PERCENT_RE = re.compile(r"(\d+(?:[.,]\d+)?)\s*%")
_DECIMAL_RE = re.compile(r"\b(\d+)[.,](\d+)\b")
_NUMBER_RE = re.compile(r"\d+")


def _words(number: int) -> str:
    return num2words(number, lang="ru")


def _decimal(whole: str, frac: str) -> str:
    # «2,5» → «два и пять»: проще и естественнее для слуха, чем «целых/десятых».
    return f"{_words(int(whole))} и {_words(int(frac))}"


def _number(value: str) -> str:
    if "," in value or "." in value:
        whole, frac = re.split(r"[.,]", value, maxsplit=1)
        return _decimal(whole, frac)
    return _words(int(value))


def normalize_for_tts(text: str) -> str:
    text = _TIME_RE.sub(lambda m: f"{_words(int(m[1]))} {_words(int(m[2])) if m[2] != '00' else 'ноль ноль'}", text)
    text = _PERCENT_RE.sub(lambda m: f"{_number(m[1])} процентов", text)
    text = _UNIT_RE.sub(lambda m: f"{_number(m[1])} {_UNITS[m[2].lower()]}", text)
    text = _DECIMAL_RE.sub(lambda m: _decimal(m[1], m[2]), text)
    text = _NUMBER_RE.sub(lambda m: _words(int(m[0])) if len(m[0]) <= 12 else " ".join(m[0]), text)
    for latin, spoken in _LATIN.items():
        text = re.sub(rf"\b{re.escape(latin)}\b", spoken, text, flags=re.IGNORECASE)
    return " ".join(text.split())
