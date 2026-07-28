"""Сводная аналитика для раздела «Аналитика» (дашборд).

Считается из двух источников:
  - таблица УСВО (`UsvoStore`) — статика по людям (безработные, охват организаций,
    давность контакта, востребованные меры поддержки);
  - обращения и заявки на приём (из веб-сервиса / SQLite) — динамика обращений.

Чистые функции без побочных эффектов — легко тестировать и переиспользовать.
"""
from __future__ import annotations

import datetime as dt
import time
from collections import Counter

from app.common.timez import msk_datetime, msk_today
from app.web.topics import classify_topic
from app.web.usvo import UsvoRecord

# ============================================================================
# Демо-наполнение дашборда (фейковые данные «для красивых дашбордов»).
# Реальные числа (обращения, заявления) суммируются с этим базисом, чтобы графики
# выглядели наполненными. Базис ДЕТЕРМИНИРОВАН — числа стабильны между запросами.
# ============================================================================

# Тематики обращений (фейковый базис) — суммируется с реальными.
FAKE_TOPICS = {
    "Выплаты и компенсации": 142,
    "Льготы и меры поддержки": 118,
    "Медицина и реабилитация": 97,
    "Трудоустройство": 64,
    "Образование и переобучение": 58,
    "Жильё": 51,
    "Юридическая помощь": 44,
    "Статус ветерана": 39,
    "Психологическая помощь": 31,
    "Запись на приём": 27,
}

FAKE_MEASURES = {
    "Ежемесячная денежная компенсация ЖКУ": 96,
    "Единовременная выплата участнику СВО": 88,
    "Содействие в трудоустройстве": 73,
    "Санаторно-курортное лечение": 61,
    "Профобучение и переподготовка": 52,
    "Технические средства реабилитации": 47,
    "Психологическая помощь": 38,
    "Юридическая помощь": 34,
}

# Динамика обращений за 14 дней — реалистичная «пила» (будни выше выходных).
FAKE_TREND_BASE = [38, 41, 44, 47, 52, 21, 18, 49, 55, 58, 53, 60, 26, 22]

# Тепловая карта Ленинского городского округа: очаги обращений.
# Координаты lat,lng — реальные географические координаты населённых пунктов
# Ленинского городского округа Московской области; intensity 0..1 — плотность очага.
LENINSKY_CENTER = {"lat": 55.5560, "lng": 37.7180, "zoom": 12}
LENINSKY_HOTSPOTS = [
    {"name": "Видное (центр)", "lat": 55.5550, "lng": 37.7030, "intensity": 1.0, "count": 184},
    {"name": "Расторгуево", "lat": 55.5660, "lng": 37.7390, "intensity": 0.78, "count": 121},
    {"name": "Совхоз им. Ленина", "lat": 55.5980, "lng": 37.7340, "intensity": 0.66, "count": 96},
    {"name": "Развилка", "lat": 55.5800, "lng": 37.7440, "intensity": 0.71, "count": 104},
    {"name": "Дрожжино", "lat": 55.5660, "lng": 37.6360, "intensity": 0.84, "count": 138},
    {"name": "Булатниково", "lat": 55.5870, "lng": 37.6650, "intensity": 0.52, "count": 74},
    {"name": "Молоково", "lat": 55.5530, "lng": 37.8060, "intensity": 0.47, "count": 63},
    {"name": "Горки Ленинские", "lat": 55.5030, "lng": 37.7720, "intensity": 0.61, "count": 88},
    {"name": "Картино", "lat": 55.5240, "lng": 37.6940, "intensity": 0.44, "count": 57},
    {"name": "Тарычёво", "lat": 55.5400, "lng": 37.7200, "intensity": 0.69, "count": 99},
]

# Комментарий ИИ под тепловой картой — выявленная новая тематика.
HEATMAP_AI_INSIGHT = (
    "Выявлена новая тематика обращений — массовые отказы в принятии заявлений на "
    "зачисление ребёнка в 1 класс. Очаги: Дрожжино, Развилка, Видное (центр). "
    "За последние 7 дней — рост в 3,4 раза. Рекомендуется проверить работу приёмных "
    "комиссий школ №№ 2, 7, 11 и подготовить разъяснение для семей участников СВО."
)


def _support_measures(records: list[UsvoRecord]) -> list[dict]:
    """Наиболее востребованные меры поддержки — из таблицы УСВО."""
    counter: Counter = Counter()
    for r in records:
        if r.flags.get("unemployed"):
            counter["Содействие в трудоустройстве"] += 1
        need_edu = any("дополнительное образование" in f.label.lower() and
                       ("нужно" in f.value.lower() or "требует" in f.value.lower())
                       for f in r.primary)
        if need_edu:
            counter["Профобучение и переподготовка"] += 1
        # Свободный текст «в каких мерах поддержки нуждаются».
        free = (r.flags.get("support_need") or "").lower()
        for key, label in (
            ("жил", "Жилищные вопросы"),
            ("выплат", "Оформление выплат"),
            ("льгот", "Льготы и компенсации"),
            ("медиц", "Медицинская помощь"),
            ("реабилит", "Реабилитация"),
            ("психолог", "Психологическая помощь"),
            ("юрид", "Юридическая помощь"),
            ("санатор", "Санаторно-курортное лечение"),
        ):
            if key in free:
                counter[label] += 1
        # Здоровье/инвалидность → ТСР и медицина.
        for f in r.primary:
            lv = f.value.lower()
            if "инвалид" in lv or "ампутац" in lv or "увеч" in lv:
                counter["Медицинская и социальная реабилитация"] += 1
                break
    return [{"label": k, "count": v} for k, v in counter.most_common(8)]


def _period_counts(timestamps: list[float]) -> dict:
    now = time.time()
    day = now - 86400
    week = now - 7 * 86400
    month = now - 30 * 86400
    return {
        "day": sum(1 for t in timestamps if t >= day),
        "week": sum(1 for t in timestamps if t >= week),
        "month": sum(1 for t in timestamps if t >= month),
        "total": len(timestamps),
    }


def _trend(timestamps: list[float], days: int = 14, end: dt.date | None = None) -> list[dict]:
    """Динамика обращений по дням за N дней до `end` включительно (по умолчанию — сегодня)."""
    end = end or msk_today()
    buckets = {end - dt.timedelta(days=i): 0 for i in range(days)}
    for t in timestamps:
        d = msk_datetime(t).date()
        if d in buckets:
            buckets[d] += 1
    ordered = sorted(buckets.items())
    return [{"date": d.strftime("%d.%m"), "count": c} for d, c in ordered]


def _merge_counts(real: list[dict], fake: dict[str, int]) -> list[dict]:
    """Складывает реальные счётчики с фейковым базисом и сортирует по убыванию."""
    merged = Counter(fake)
    for item in real:
        merged[item["label"]] += item["count"]
    return [{"label": k, "count": v} for k, v in merged.most_common(10)]


# ============================================================================
# Временные ряды для интерактивного графика «Динамика по показателям».
# Детерминированно: значения зависят только от даты (стабильны в течение дня).
# Реальные обращения добавляются к базису за окно графика.
#
# Окно задаёт фронтенд (`/analytics?days=&end=`): «1 день» (тогда рисуется
# ПОЧАСОВОЙ разрез), «7 дней» с выбором недели и «месяц» с выбором месяца.
# Поэтому все генераторы принимают `end` — конец окна, а не жёстко «сегодня».
# ============================================================================

MAX_SERIES_DAYS = 92  # верхняя граница окна (≈ квартал) — защита от больших запросов


def _appeals_daily(days_total: int, end: dt.date | None = None) -> list[int]:
    """Дневной ряд обращений за `days_total` дней до `end` (включительно).

    Будни выше выходных + детерминированная «дрожь» по дню. Без случайностей —
    одинаковый результат при каждом запросе одного и того же окна.
    """
    end = end or dt.date.today()
    weekday_base = [60, 58, 55, 57, 62, 30, 24]  # Пн..Вс
    out: list[int] = []
    for i in range(days_total - 1, -1, -1):
        d = end - dt.timedelta(days=i)
        wob = (d.toordinal() * 7919) % 17 - 8
        out.append(max(5, weekday_base[d.weekday()] + wob))
    return out


def _aux_daily(seed: int, weekday_base: list[int], days: int = 14,
               end: dt.date | None = None) -> list[int]:
    """Вспомогательный дневной ряд (очные обращения, заявления и т. п.)."""
    end = end or dt.date.today()
    out: list[int] = []
    for i in range(days - 1, -1, -1):
        d = end - dt.timedelta(days=i)
        out.append(max(0, weekday_base[d.weekday()] + (d.toordinal() * seed) % 7))
    return out


def _series_points(dates: list[str], values: list[int]) -> list[dict]:
    return [{"date": dates[k], "count": int(values[k])} for k in range(len(dates))]


# Суточный профиль нагрузки контакт-центра (ночь тихая, пик — утро и день).
_HOURLY_WEIGHTS = [1, 1, 1, 1, 2, 3, 5, 8, 12, 14, 13, 12,
                   11, 12, 13, 12, 10, 8, 6, 4, 3, 2, 2, 1]


def _distribute_hourly(
    day_total: int,
    seed: int,
    appeal_times: list[float] | None = None,
    day: dt.date | None = None,
) -> list[dict]:
    """Почасовой разрез одних суток (подписи «HH:00») для режима «1 день».

    Дневной итог раскладывается по профилю `_HOURLY_WEIGHTS`, остаток от
    округления доводится по кругу от `seed` (чтобы сумма сошлась), затем
    добавляются РЕАЛЬНЫЕ обращения этих суток по московскому часу.
    """
    day = day or msk_today()
    wsum = sum(_HOURLY_WEIGHTS) or 1
    base = [max(0, int(round(day_total * w / wsum))) for w in _HOURLY_WEIGHTS]
    diff = day_total - sum(base)
    i = 0
    while diff != 0 and i < 24 * 8:
        idx = (seed + i) % 24
        if diff > 0:
            base[idx] += 1
            diff -= 1
        elif base[idx] > 0:
            base[idx] -= 1
            diff += 1
        i += 1
    if appeal_times:
        for t in appeal_times:
            local = msk_datetime(t)
            if local.date() == day:
                base[local.hour] += 1
    for h in range(24):
        wob = ((seed * 17 + h * 13 + day.toordinal()) % 5) - 2
        base[h] = max(0, base[h] + wob)
    return [{"date": f"{h:02d}:00", "count": int(base[h])} for h in range(24)]


def _build_series(
    appeal_times: list[float],
    days: int = 30,
    end: dt.date | None = None,
) -> list[dict]:
    """Набор переключаемых временных рядов (x — дата, y — значение показателя).

    У каждого ряда есть и дневные `points` (окно `days` до `end`), и `hourly` —
    разрез последних суток окна: фронтенд переключается на него в режиме «1 день».
    """
    end = end or dt.date.today()
    days = max(1, min(int(days), MAX_SERIES_DAYS))
    # Запас слева нужен скользящим окнам 7 и 30 дней (иначе первые точки просядут).
    total = max(60, days + 30)
    daily = _appeals_daily(total, end=end)
    # Реальные обращения за окно — поверх базиса.
    real = _trend(appeal_times, days, end=end)  # oldest → newest, длина days
    for k in range(days):
        daily[total - days + k] += real[k]["count"]

    dates = [(end - dt.timedelta(days=days - 1 - k)).strftime("%d.%m") for k in range(days)]

    base = total - days  # индекс начала окна
    day_vals = [daily[base + k] for k in range(days)]
    week_vals = [sum(daily[max(0, base + k - 6): base + k + 1]) for k in range(days)]
    month_vals = [sum(daily[max(0, base + k - 29): base + k + 1]) for k in range(days)]
    in_person_vals = _aux_daily(1013, [14, 13, 12, 13, 15, 5, 3], days, end=end)
    apps_vals = _aux_daily(2027, [9, 8, 7, 8, 10, 3, 2], days, end=end)

    return [
        {"key": "day", "label": "Обращений за день",
         "unit": "обр./день", "unit_hourly": "обр./час",
         "points": _series_points(dates, day_vals),
         "hourly": _distribute_hourly(day_vals[-1], 401, appeal_times, day=end)},
        {"key": "week", "label": "Обращений за неделю",
         "unit": "обр./7 дн.", "unit_hourly": "обр./час",
         "points": _series_points(dates, week_vals),
         "hourly": _distribute_hourly(max(1, week_vals[-1] // 7), 503, None, day=end)},
        {"key": "month", "label": "Обращений за месяц",
         "unit": "обр./30 дн.", "unit_hourly": "обр./час",
         "points": _series_points(dates, month_vals),
         "hourly": _distribute_hourly(max(1, month_vals[-1] // 30), 607, None, day=end)},
        {"key": "in_person", "label": "Очных обращений в администрацию",
         "unit": "визитов/день", "unit_hourly": "визитов/час",
         "points": _series_points(dates, in_person_vals),
         "hourly": _distribute_hourly(in_person_vals[-1], 1013, None, day=end)},
        {"key": "applications", "label": "Заявления на меры поддержки",
         "unit": "заявл./день", "unit_hourly": "заявл./час",
         "points": _series_points(dates, apps_vals),
         "hourly": _distribute_hourly(apps_vals[-1], 2027, None, day=end)},
    ]


def build_analytics(
    records: list[UsvoRecord],
    appeal_times: list[float],
    appeal_topics: list[str],
    appointment_count: int,
    stale_days: int,
    applications: int = 0,
    series_days: int = 30,
    series_end: dt.date | None = None,
) -> dict:
    total = len(records)
    unemployed = sum(1 for r in records if r.flags.get("unemployed"))
    stale = sum(1 for r in records if r.flags.get("stale_contact"))

    org_vremya = sum(1 for r in records if r.flags.get("org_vremya"))
    org_geroi = sum(1 for r in records if r.flags.get("org_geroi_mo"))
    org_assoc = sum(1 for r in records if r.flags.get("org_assoc"))
    org_any = sum(
        1 for r in records
        if r.flags.get("org_vremya") or r.flags.get("org_geroi_mo") or r.flags.get("org_assoc")
    )

    topic_counter = Counter(appeal_topics)
    topics = _merge_counts(
        [{"label": k, "count": v} for k, v in topic_counter.items()], FAKE_TOPICS
    )
    measures = _merge_counts(_support_measures(records), FAKE_MEASURES)

    status_counter = Counter(
        (r.status or "—").strip().capitalize() for r in records if r.status
    )
    statuses = [{"label": k, "count": v} for k, v in status_counter.most_common(8)]

    # Обращения: реальные периоды + фейковый базис «для красивых дашбордов».
    real_periods = _period_counts(appeal_times)
    appeals = {
        "day": real_periods["day"] + 47,
        "week": real_periods["week"] + 286,
        "month": real_periods["month"] + 1124,
        "total": real_periods["total"] + 3960,
    }

    # Динамика за 14 дней: реальные + фейковый базис.
    real_trend = _trend(appeal_times)
    trend = [
        {"date": t["date"], "count": t["count"] + FAKE_TREND_BASE[i % len(FAKE_TREND_BASE)]}
        for i, t in enumerate(real_trend)
    ]

    return {
        "appeals": appeals,
        "appeals_trend": trend,
        "series": _build_series(appeal_times, days=series_days, end=series_end),
        "topics": topics,
        "support_measures": measures,
        "in_person": appointment_count + 178,
        "applications": applications + 64,
        "orgs": {
            "vremya_geroev": org_vremya,
            "geroi_podmoskovya": org_geroi,
            "associaciya": org_assoc,
            "covered": org_any,
            "total": total,
            "coverage_pct": round(org_any / total * 100) if total else 0,
        },
        "unemployed": unemployed,
        "stale_contacts": stale,
        "stale_days": stale_days,
        "total_usvo": total,
        "statuses": statuses,
        "heatmap": {
            "area": "Ленинский городской округ",
            "center": LENINSKY_CENTER,
            "hotspots": LENINSKY_HOTSPOTS,
            "ai_insight": HEATMAP_AI_INSIGHT,
        },
    }
