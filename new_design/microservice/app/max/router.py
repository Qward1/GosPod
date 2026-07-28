"""Вебхук MAX: принимает апдейты (сообщения и нажатия inline-кнопок)."""
from __future__ import annotations

import logging

from fastapi import APIRouter, Request

from app.max.bot_logic import MaxBot

log = logging.getLogger("max.router")

router = APIRouter(prefix="/max", tags=["max"])


@router.post("/webhook")
async def webhook(request: Request) -> dict:
    """Точка приёма апдейтов MAX Bot API."""
    bot: MaxBot = request.app.state.bot
    try:
        update = await request.json()
    except Exception:  # noqa: BLE001
        log.warning("Не удалось разобрать тело вебхука как JSON")
        return {"ok": False}

    # Логируем тип апдейта — удобно убедиться, что MAX вообще доставляет события.
    log.info("MAX update получен: type=%s", update.get("update_type") or update.get("type"))
    await bot.handle_update(update)
    return {"ok": True}
