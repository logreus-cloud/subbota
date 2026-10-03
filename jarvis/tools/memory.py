from __future__ import annotations

import asyncio
import os
import tempfile
import threading
from pathlib import Path
from typing import TYPE_CHECKING, Annotated

from claude_agent_sdk import SdkMcpTool, tool

if TYPE_CHECKING:
    from jarvis.context import AppContext


_LOCK = threading.Lock()


def _text(value: str, error: bool = False) -> dict:
    result = {"content": [{"type": "text", "text": value}]}
    if error:
        result["is_error"] = True
    return result


def _read(path: Path) -> list[str]:
    try:
        return path.read_text(encoding="utf-8").splitlines()
    except FileNotFoundError:
        return []


def _write(path: Path, lines: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", newline="\n", dir=path.parent,
            prefix=".memory-", suffix=".tmp", delete=False,
        ) as stream:
            temporary = Path(stream.name)
            stream.write("\n".join(lines) + ("\n" if lines else ""))
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def _normalize(value: str) -> str:
    return " ".join(value.split())


def _remember(path: Path, fact: str) -> str:
    fact = _normalize(fact)
    if fact.startswith("- "):
        fact = fact[2:].strip()
    if not fact:
        raise ValueError("Факт не должен быть пустым.")
    with _LOCK:
        lines = _read(path)
        line = f"- {fact}"
        if any(_normalize(existing).casefold() == line.casefold() for existing in lines):
            return "Этот факт уже сохранён."
        lines.append(line)
        _write(path, lines)
    return "Факт сохранён."


def _forget(path: Path, query: str) -> str:
    query = _normalize(query).casefold()
    if not query:
        raise ValueError("Укажите текст для поиска.")
    with _LOCK:
        lines = _read(path)
        removed = [line for line in lines if query in _normalize(line).casefold()]
        if not removed:
            return "Подходящих фактов нет."
        _write(path, [line for line in lines if query not in _normalize(line).casefold()])
    return "Удалено:\n" + "\n".join(removed)


def _recall(path: Path) -> str:
    with _LOCK:
        lines = _read(path)
    return "\n".join(lines) if lines else "Сохранённых фактов нет."


def make_tools(ctx: AppContext) -> list[SdkMcpTool]:
    async def run(func, *args):
        try:
            path = await asyncio.to_thread(lambda: ctx.cfg.data_dir / "memory.md")
            return _text(await asyncio.to_thread(func, path, *args))
        except Exception as exc:
            return _text(f"Ошибка: {exc}", True)

    @tool("remember", "Сохранить факт о пользователе.", {
        "fact": Annotated[str, "Один факт для сохранения."],
    })
    async def remember(args):
        return await run(_remember, args["fact"])

    @tool("forget", "Удалить факты, содержащие указанный текст.", {
        "query": Annotated[str, "Часть факта для удаления."],
    })
    async def forget(args):
        return await run(_forget, args["query"])

    @tool("recall", "Прочитать сохранённые факты.", {})
    async def recall(args):
        return await run(_recall)

    return [remember, forget, recall]
