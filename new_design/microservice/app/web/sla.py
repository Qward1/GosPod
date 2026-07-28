"""Расчёт SLA (регламентного срока) обращений граждан."""
from __future__ import annotations

from app.common.timez import msk_datetime, msk_now


def business_deadline(created_ts: float, business_days: int) -> float | None:
    """Дедлайн = создание + N календарных дней; None, если нет времени создания."""
    if not created_ts:
        return None
    start = msk_datetime(created_ts)
    days = max(0, int(business_days))
    return start.timestamp() + days * 86400


def _human_age(seconds: float) -> str:
    """Возраст обращения человекочитаемо (для UI-подсказок и отчётов)."""
    if seconds < 0:
        seconds = 0
    minutes = int(seconds // 60)
    if minutes < 60:
        return f"{minutes} мин"
    hours = minutes // 60
    if hours < 24:
        return f"{hours} ч"
    days = hours // 24
    rem_hours = hours % 24
    return f"{days} дн" + (f" {rem_hours} ч" if rem_hours else "")


def sla_fields(
    created_ts: float,
    *,
    business_days: int = 3,
    status: str = "open",
    now: float | None = None,
) -> dict:
    """Поля SLA для одного обращения."""
    now = now if now is not None else msk_now().timestamp()
    if not created_ts:
        return {"age": "", "age_days": None, "deadline_at": None, "is_overdue": False}
    age_seconds = max(0.0, now - float(created_ts))
    deadline = business_deadline(created_ts, business_days)
    resolved = (status or "").strip().lower() in ("answered", "closed", "resolved")
    is_overdue = bool(deadline is not None and now > deadline and not resolved)
    return {
        "age": _human_age(age_seconds),
        "age_days": int(age_seconds // 86400),
        "deadline_at": deadline,
        "is_overdue": is_overdue,
    }
