"""Цикл long polling — общий для maxctl.py и фонового режима в uvicorn.

Long polling нужен, когда нет публичного HTTPS-вебхука (локальная разработка): сервис
сам ходит в MAX за событиями. НЕ запускайте polling одновременно с активной подпиской
на вебхук — событие заберёт только один канал.
"""
from __future__ import annotations

import asyncio
import logging

from app.max.bot_logic import MaxBot
from app.max.client import MaxClient

log = logging.getLogger("max.polling")


async def poll_loop(bot: MaxBot, client: MaxClient, poll_timeout: int = 30) -> None:
    log.info("Long polling запущен (timeout=%ss).", poll_timeout)
    marker: int | None = None
    while True:
        try:
            data = await client.get_updates(marker=marker, timeout=poll_timeout)
            updates = data.get("updates", []) if isinstance(data, dict) else []
            for upd in updates:
                log.info("MAX update (poll): type=%s", upd.get("update_type") or upd.get("type"))
                await bot.handle_update(upd)
            if isinstance(data, dict) and data.get("marker") is not None:
                marker = data["marker"]
        except asyncio.CancelledError:
            log.info("Long polling остановлен.")
            raise
        except Exception:  # noqa: BLE001 — не роняем цикл из-за разовой ошибки сети
            log.exception("Ошибка в цикле polling, повтор через 3с")
            await asyncio.sleep(3)
