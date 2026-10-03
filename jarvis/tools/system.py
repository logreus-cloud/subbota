from __future__ import annotations

import asyncio
import base64
import ctypes
import io
import os
import subprocess
import threading
import time
import webbrowser
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Annotated
from urllib.parse import urlsplit

from claude_agent_sdk import SdkMcpTool, tool

if TYPE_CHECKING:
    from jarvis.context import AppContext


_APP_LOCK = threading.Lock()
_APP_CACHE: dict[tuple[str, str], tuple[float, list[tuple[str, str]]]] = {}
_ACTIONS = {
    "play_pause": "VK_MEDIA_PLAY_PAUSE",
    "next": "VK_MEDIA_NEXT_TRACK",
    "previous": "VK_MEDIA_PREV_TRACK",
    "stop": "VK_MEDIA_STOP",
    "volume_up": "VK_VOLUME_UP",
    "volume_down": "VK_VOLUME_DOWN",
}


def _text(value: str, error: bool = False) -> dict:
    result = {"content": [{"type": "text", "text": value}]}
    if error:
        result["is_error"] = True
    return result


async def _call(func, *args) -> dict:
    try:
        return _text(await asyncio.to_thread(func, *args))
    except Exception as exc:
        return _text(f"Ошибка: {exc}", True)


def _audio(operation: str, value: int | bool | None = None) -> str:
    import comtypes
    from comtypes import CLSCTX_ALL
    from pycaw.pycaw import AudioUtilities, IAudioEndpointVolume

    comtypes.CoInitialize()
    try:
        speakers = AudioUtilities.GetSpeakers()
        endpoint = getattr(speakers, "EndpointVolume", None)
        if endpoint is None:
            interface = speakers.Activate(IAudioEndpointVolume._iid_, CLSCTX_ALL, None)
            endpoint = ctypes.cast(interface, ctypes.POINTER(IAudioEndpointVolume))
        if operation == "get":
            level = round(endpoint.GetMasterVolumeLevelScalar() * 100)
            muted = bool(endpoint.GetMute())
            return f"Громкость: {level}%. Звук {'выключен' if muted else 'включён'}."
        if operation == "set":
            endpoint.SetMasterVolumeLevelScalar(value / 100, None)
            return f"Громкость установлена на {value}%."
        endpoint.SetMute(value, None)
        return "Звук выключен." if value else "Звук включён."
    finally:
        comtypes.CoUninitialize()


def _media(action: str) -> str:
    import win32api
    import win32con

    key = getattr(win32con, _ACTIONS[action])
    win32api.keybd_event(key, 0, 0, 0)
    win32api.keybd_event(key, 0, win32con.KEYEVENTF_KEYUP, 0)
    return f"Медиа-команда {action} отправлена."


def _apps(ctx: AppContext) -> list[tuple[str, str]]:
    roots = (
        os.environ.get("ProgramData", "") + r"\Microsoft\Windows\Start Menu\Programs",
        os.environ.get("APPDATA", "") + r"\Microsoft\Windows\Start Menu\Programs",
    )
    key = (roots[0], roots[1])
    with _APP_LOCK:
        cached = _APP_CACHE.get(key)
        if cached is not None and time.monotonic() - cached[0] < 300:
            return list(cached[1])
        entries = [(str(name), str(path)) for name, path in ctx.cfg.apps.items()]
        for root in roots:
            if not root:
                continue
            folder = Path(root)
            if folder.is_dir():
                for path in folder.rglob("*"):
                    if path.suffix.lower() in {".lnk", ".url"}:
                        entries.append((path.stem, str(path)))
        entries.sort(key=lambda item: (item[0].casefold(), item[1].casefold()))
        _APP_CACHE[key] = (time.monotonic(), entries)
        return list(entries)


def _matches(ctx: AppContext, query: str) -> list[tuple[str, str]]:
    needle = query.casefold()
    return [item for item in _apps(ctx) if needle in item[0].casefold()]


def _find_apps(ctx: AppContext, query: str) -> str:
    matches = _matches(ctx, query)[:15]
    if not matches:
        return "Приложения не найдены."
    return "\n".join(f"{name} — {path}" for name, path in matches)


def _open_app(ctx: AppContext, name: str) -> str:
    if not name:
        raise ValueError("Укажите название приложения.")
    aliases = ctx.cfg.apps
    target = next((str(path) for alias, path in aliases.items() if alias.casefold() == name.casefold()), None)
    label = name
    if target is None:
        matches = _matches(ctx, name)
        if not matches:
            words = name.casefold().split()
            nearby = [item[0] for item in _apps(ctx) if any(word in item[0].casefold() for word in words)]
            choices = ", ".join(dict.fromkeys(nearby[:5]))
            raise ValueError(f"Приложение не найдено.{f' Возможные варианты: {choices}.' if choices else ''}")
        label, target = min(
            matches,
            key=lambda item: (
                0 if item[0].casefold() == name.casefold() else
                1 if item[0].casefold().startswith(name.casefold()) else 2,
                len(item[0]),
                item[0].casefold(),
            ),
        )
    os.startfile(target)
    return f"Открыто: {label}."


def _open_url(url: str) -> str:
    parsed = urlsplit(url)
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.netloc:
        raise ValueError("Нужен полный адрес http:// или https://.")
    if not webbrowser.open(url):
        raise RuntimeError("Браузер не открыл ссылку.")
    return "Ссылка открыта."


def _windows() -> list[tuple[int, str, str]]:
    import psutil
    import win32gui
    import win32process

    windows = []

    def collect(hwnd, _):
        if not win32gui.IsWindowVisible(hwnd):
            return
        title = win32gui.GetWindowText(hwnd).strip()
        if not title:
            return
        try:
            _, pid = win32process.GetWindowThreadProcessId(hwnd)
            process = psutil.Process(pid).name()
        except (psutil.Error, OSError):
            process = "неизвестен"
        windows.append((hwnd, title, process))

    win32gui.EnumWindows(collect, None)
    return windows


def _list_windows() -> str:
    windows = _windows()[:40]
    return "\n".join(f"{title} — {process}" for _, title, process in windows) or "Открытых окон не найдено."


def _window(title: str, close: bool) -> str:
    import win32api
    import win32con
    import win32gui

    if not title:
        raise ValueError("Укажите заголовок окна.")
    match = next(((hwnd, name) for hwnd, name, _ in _windows() if title.casefold() in name.casefold()), None)
    if match is None:
        raise ValueError("Окно не найдено.")
    hwnd, name = match
    if close:
        win32gui.PostMessage(hwnd, win32con.WM_CLOSE, 0, 0)
        return f"Окну «{name}» отправлена команда закрытия."
    if win32gui.IsIconic(hwnd):
        win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
    win32api.keybd_event(win32con.VK_MENU, 0, 0, 0)
    try:
        win32gui.SetForegroundWindow(hwnd)
    finally:
        win32api.keybd_event(win32con.VK_MENU, 0, win32con.KEYEVENTF_KEYUP, 0)
    return f"Активно окно «{name}»."


def _clipboard(text: str | None = None) -> str:
    import win32clipboard
    import win32con

    win32clipboard.OpenClipboard()
    try:
        if text is None:
            if not win32clipboard.IsClipboardFormatAvailable(win32con.CF_UNICODETEXT):
                raise ValueError("В буфере обмена нет текста.")
            return str(win32clipboard.GetClipboardData(win32con.CF_UNICODETEXT))[:5000]
        win32clipboard.EmptyClipboard()
        win32clipboard.SetClipboardData(win32con.CF_UNICODETEXT, text)
        return "Текст скопирован в буфер обмена."
    finally:
        win32clipboard.CloseClipboard()


def _screenshot() -> str:
    from PIL import ImageGrab

    image = ImageGrab.grab(all_screens=True)
    if image.width > 1600:
        image.thumbnail((1600, image.height))
    output = io.BytesIO()
    image.save(output, format="PNG")
    return base64.b64encode(output.getvalue()).decode("ascii")


def _system_info() -> str:
    import psutil

    cpu = psutil.cpu_percent(interval=None)
    ram = psutil.virtual_memory()
    disks = []
    for partition in psutil.disk_partitions():
        try:
            usage = psutil.disk_usage(partition.mountpoint)
        except (psutil.Error, OSError):
            continue
        disks.append(f"{partition.mountpoint} {usage.percent}%")
    battery = psutil.sensors_battery()
    uptime = int(time.time() - psutil.boot_time())
    processes = []
    for process in psutil.process_iter(["name", "memory_info"]):
        try:
            info = process.info
            memory = info["memory_info"]
            if memory is not None:
                processes.append((memory.rss, info["name"] or "неизвестен"))
        except (psutil.Error, OSError):
            continue
    processes.sort(reverse=True)
    top = ", ".join(f"{name} {size // 1048576} МБ" for size, name in processes[:5])
    lines = [
        f"CPU: {cpu}%. RAM: {ram.percent}% ({ram.used // 1073741824}/{ram.total // 1073741824} ГБ).",
        f"Диски: {', '.join(disks) or 'нет данных'}.",
        f"Аптайм: {uptime // 86400} д. {(uptime % 86400) // 3600} ч.",
        f"Топ процессов по памяти: {top or 'нет данных'}.",
    ]
    if battery is not None:
        lines.insert(2, f"Батарея: {battery.percent}%{' (заряжается)' if battery.power_plugged else ''}.")
    return "\n".join(lines)


def _power(action: str) -> str:
    if action == "lock":
        if not ctypes.windll.user32.LockWorkStation():
            raise OSError("Не удалось заблокировать компьютер.")
        return "Компьютер заблокирован."
    if action == "sleep":
        subprocess.run(["rundll32.exe", "powrprof.dll,SetSuspendState", "0,1,0"], check=True)
        return "Компьютер отправлен в сон."
    switch = "/r" if action == "restart" else "/s"
    subprocess.run(["shutdown", switch, "/t", "30"], check=True)
    return "Перезагрузка через 30 секунд." if action == "restart" else "Выключение через 30 секунд."


def make_tools(ctx: AppContext) -> list[SdkMcpTool]:
    @tool("set_volume", "Установить громкость в процентах.", {
        "type": "object", "properties": {
            "level": {"type": "integer", "minimum": 0, "maximum": 100, "description": "Громкость от 0 до 100."},
        }, "required": ["level"],
    })
    async def set_volume(args):
        return await _call(_audio, "set", args["level"])

    @tool("get_volume", "Узнать громкость и состояние звука.", {})
    async def get_volume(args):
        return await _call(_audio, "get")

    @tool("set_mute", "Включить или выключить звук.", {
        "muted": Annotated[bool, "Выключить звук, если true."],
    })
    async def set_mute(args):
        return await _call(_audio, "mute", args["muted"])

    @tool("media_control", "Управлять воспроизведением и громкостью.", {
        "type": "object", "properties": {
            "action": {"type": "string", "enum": list(_ACTIONS), "description": "Медиа-команда."},
        }, "required": ["action"],
    })
    async def media_control(args):
        return await _call(_media, args["action"])

    @tool("find_apps", "Найти приложения в меню «Пуск» и среди алиасов.", {
        "query": Annotated[str, "Часть названия приложения."],
    })
    async def find_apps(args):
        return await _call(_find_apps, ctx, args["query"].strip())

    @tool("open_app", "Открыть приложение по названию или алиасу.", {
        "name": Annotated[str, "Название приложения или алиас."],
    })
    async def open_app(args):
        return await _call(_open_app, ctx, args["name"].strip())

    @tool("open_url", "Открыть ссылку http или https в браузере.", {
        "url": Annotated[str, "Полная ссылка http:// или https://."],
    })
    async def open_url(args):
        return await _call(_open_url, args["url"])

    @tool("list_windows", "Показать открытые окна и их процессы.", {})
    async def list_windows(args):
        return await _call(_list_windows)

    @tool("focus_window", "Переключиться на окно по части заголовка.", {
        "title": Annotated[str, "Часть заголовка окна."],
    })
    async def focus_window(args):
        return await _call(_window, args["title"].strip(), False)

    @tool("close_window", "Закрыть окно по части заголовка.", {
        "title": Annotated[str, "Часть заголовка окна."],
    })
    async def close_window(args):
        return await _call(_window, args["title"].strip(), True)

    @tool("get_clipboard", "Прочитать текст из буфера обмена.", {})
    async def get_clipboard(args):
        return await _call(_clipboard)

    @tool("set_clipboard", "Записать текст в буфер обмена.", {
        "text": Annotated[str, "Текст для буфера обмена."],
    })
    async def set_clipboard(args):
        return await _call(_clipboard, args["text"])

    @tool("take_screenshot", "Сделать снимок всех экранов.", {})
    async def take_screenshot(args):
        try:
            data = await asyncio.to_thread(_screenshot)
            return {"content": [
                {"type": "text", "text": "Снимок экрана."},
                {"type": "image", "data": data, "mimeType": "image/png"},
            ]}
        except Exception as exc:
            return _text(f"Ошибка: {exc}", True)

    @tool("system_info", "Узнать загрузку компьютера, диски и процессы.", {})
    async def system_info(args):
        return await _call(_system_info)

    @tool("power_action", "Заблокировать, усыпить, перезагрузить или выключить компьютер.", {
        "type": "object", "properties": {
            "action": {"type": "string", "enum": ["lock", "sleep", "restart", "shutdown"], "description": "Действие с питанием."},
        }, "required": ["action"],
    })
    async def power_action(args):
        result = await _call(_power, args["action"])
        if not result.get("is_error") and args["action"] in {"restart", "shutdown"}:
            result["content"][0]["text"] += " Отмена: shutdown /a."
        return result

    return [
        set_volume, get_volume, set_mute, media_control, find_apps, open_app,
        open_url, list_windows, focus_window, close_window, get_clipboard,
        set_clipboard, take_screenshot, system_info, power_action,
    ]
