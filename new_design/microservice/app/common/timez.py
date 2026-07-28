"""Единое московское время для всего кабинета."""
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
