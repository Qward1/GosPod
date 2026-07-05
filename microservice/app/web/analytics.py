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


def _trend(timestamps: list[float], days: int = 14) -> list[dict]:
    """Динамика обращений по дням за последние N дней (для мини-графика)."""
    today = dt.date.today()
    buckets = {today - dt.timedelta(days=i): 0 for i in range(days)}
    for t in timestamps:
        d = dt.datetime.fromtimestamp(t).date()
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
# Реальные обращения добавляются к базису за последние 14 дней.
# ============================================================================

def _appeals_daily(days_total: int) -> list[int]:
    """Дневной ряд обращений за `days_total` дней до сегодня (включительно).

    Будни выше выходных + детерминированная «дрожь» по дню. Без случайностей —
    одинаковый результат при каждом запросе в течение суток.
    """
    today = dt.date.today()
    weekday_base = [60, 58, 55, 57, 62, 30, 24]  # Пн..Вс
    out: list[int] = []
    for i in range(days_total - 1, -1, -1):
        d = today - dt.timedelta(days=i)
        wob = (d.toordinal() * 7919) % 17 - 8
        out.append(max(5, weekday_base[d.weekday()] + wob))
    return out


def _aux_daily(seed: int, weekday_base: list[int], days: int = 14) -> list[int]:
    """Вспомогательный дневной ряд (очные обращения, заявления и т. п.)."""
    today = dt.date.today()
    out: list[int] = []
    for i in range(days - 1, -1, -1):
        d = today - dt.timedelta(days=i)
        out.append(max(0, weekday_base[d.weekday()] + (d.toordinal() * seed) % 7))
    return out


def _series_points(dates: list[str], values: list[int]) -> list[dict]:
    return [{"date": dates[k], "count": int(values[k])} for k in range(len(dates))]


def _build_series(appeal_times: list[float], days: int = 14) -> list[dict]:
    """Набор переключаемых временных рядов (x — дата, y — значение показателя)."""
    total = 44  # запас для скользящих окон 7 и 30 дней
    daily = _appeals_daily(total)
    # Реальные обращения за последние `days` дней — поверх базиса.
    real = _trend(appeal_times, days)  # oldest → newest, длина days
    for k in range(days):
        daily[total - days + k] += real[k]["count"]

    today = dt.date.today()
    dates = [(today - dt.timedelta(days=days - 1 - k)).strftime("%d.%m") for k in range(days)]

    base = total - days  # индекс начала окна последних `days` дней
    day_vals = [daily[base + k] for k in range(days)]
    week_vals = [sum(daily[base + k - 6: base + k + 1]) for k in range(days)]
    month_vals = [sum(daily[base + k - 29: base + k + 1]) for k in range(days)]
    in_person_vals = _aux_daily(1013, [14, 13, 12, 13, 15, 5, 3], days)
    apps_vals = _aux_daily(2027, [9, 8, 7, 8, 10, 3, 2], days)

    return [
        {"key": "day", "label": "Обращений за день", "unit": "обр./день",
         "points": _series_points(dates, day_vals)},
        {"key": "week", "label": "Обращений за неделю", "unit": "обр./7 дн.",
         "points": _series_points(dates, week_vals)},
        {"key": "month", "label": "Обращений за месяц", "unit": "обр./30 дн.",
         "points": _series_points(dates, month_vals)},
        {"key": "in_person", "label": "Очных обращений в администрацию", "unit": "визитов/день",
         "points": _series_points(dates, in_person_vals)},
        {"key": "applications", "label": "Заявлений на меры поддержки", "unit": "заявл./день",
         "points": _series_points(dates, apps_vals)},
    ]


def build_analytics(
    records: list[UsvoRecord],
    appeal_times: list[float],
    appeal_topics: list[str],
    appointment_count: int,
    stale_days: int,
    applications: int = 0,
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
        "series": _build_series(appeal_times),
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
