"""Служебные эндпоинты: проверка живости и просмотр расписания."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from app.config import Config, get_config
from app.max.schedule import load_schedule

router = APIRouter(tags=["common"])


@router.get("/health")
async def health() -> dict:
    return {"status": "ok"}


@router.get("/schedule")
async def schedule(cfg: Config = Depends(get_config)) -> dict:
    """Возвращает текущее расписание (перечитывается из файла на каждый запрос)."""
    try:
        return load_schedule(cfg.schedule_file)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=f"Ошибка чтения расписания: {exc}") from exc
