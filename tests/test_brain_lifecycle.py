"""Жизненный цикл мозга без подключения к Claude: worker подменяется заглушкой."""
import asyncio
import types

from subbota.agent import Brain


def _brain():
    ctx = types.SimpleNamespace(
        bus=types.SimpleNamespace(publish=lambda *a, **k: None),
        set_status=lambda status: None,
    )
    brain = Brain(ctx)

    async def idle_worker():
        await asyncio.sleep(3600)

    brain._worker = idle_worker
    return brain


def test_cancelled_stop_still_finishes():
    async def scenario():
        brain = _brain()
        await brain.start()
        waiter = asyncio.create_task(brain.stop())
        await asyncio.sleep(0)
        waiter.cancel()
        try:
            await waiter
        except asyncio.CancelledError:
            pass
        # Остановка идёт под shield и доходит до конца (worker снимается по таймауту 5 с).
        for _ in range(80):
            if brain._state == "stopped":
                break
            await asyncio.sleep(0.1)
        assert brain._state == "stopped"
        assert brain._stop_task is None
        await brain.start()
        assert brain._state == "running"
        await brain.stop()
        assert brain._state == "stopped"

    asyncio.run(scenario())


def test_ask_after_stop_is_rejected():
    async def scenario():
        brain = _brain()
        result = await brain.ask("привет")
        assert result.is_error and result.text == "Остановлено"

    asyncio.run(scenario())
