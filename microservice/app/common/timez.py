"""Единое московское время для всего кабинета.

Сервер может работать в UTC (или любой другой зоне), а пользователи и регламенты
живут по Москве. Поэтому все ОТОБРАЖАЕМЫЕ даты/время считаем в Europe/Moscow.
Внутреннее хранение по-прежнему в unix-ts (UTC-независимо) — переводим только на
выводе.

Москва с 2014 года без перехода на летнее время — фиксированный UTC+3, поэтому
достаточно постоянного смещения (не зависим от наличия tzdata на сервере).
"""
from __future__ import annotations

import datetime as dt

# Московская зона: фиксированный UTC+3 (без DST).
MSK = dt.timezone(dt.timedelta(hours=3), name="MSK")


def msk_datetime(ts: float) -> dt.datetime:
    """unix-ts → aware-datetime в московской зоне."""
    return dt.datetime.fromtimestamp(float(ts), tz=MSK)


def msk_now() -> dt.datetime:
    """Текущий момент по Москве (aware)."""
    return dt.datetime.now(MSK)


def msk_today() -> dt.date:
    """Сегодняшняя дата по Москве."""
    return msk_now().date()


def human_ts(ts: float, fmt: str = "%d.%m.%Y %H:%M") -> str:
    """Человекочитаемое время по Москве или «—», если ts пуст."""
    if not ts:
        return "—"
    return msk_datetime(ts).strftime(fmt)
