"""Синтетическая «История взаимодействия» для карточки УСВО.

Dify/бот пишут только реальные обращения; чтобы карточка выглядела «живой» и
оператор сразу видел путь человека (обзвоны, визиты в учреждения, трудоустройство,
выплаты, реабилитация), история достраивается правдоподобными событиями.

Данные **детерминированы** по `record.id` (один и тот же человек — одна и та же
история между запросами) и **согласованы с полями карточки**: если человек
нуждается в трудоустройстве — в истории это «в работе», если уже трудоустроен —
«выполнено»; статус ВБД, инвалидность, охват организациями учитываются так же.
Реальные обращения (если есть) вливаются в ту же ленту и сортируются по дате.
"""
from __future__ import annotations

import datetime as dt
import random

from app.common.timez import msk_datetime, msk_today
from app.web.usvo import UsvoRecord

# Учреждения Подмосковья (правдоподобные названия).
ORGS = {
    "mfc": "МФЦ «Мои документы» г.о. Ленинский, г. Видное",
    "voenkomat": "Военный комиссариат Московской области",
    "soczashchita": "Управление социальной защиты населения",
    "zanyatost": "Центр занятости населения «Работа России»",
    "gospital": "Госпиталь для ветеранов войн №2, г. Москва",
    "poliklinika": "Видновская районная клиническая больница",
    "admin": "Администрация городского округа",
    "fond": "Фонд «Защитники Отечества», филиал по Московской области",
    "pfr": "Социальный фонд России (СФР)",
    "uchebcentr": "Учебный центр «Профессионалитет»",
    "sanatoriy": "Санаторий «Дорохово», Московская область",
}

# Статусы события.
DONE = "выполнено"
PROGRESS = "в работе"
PLANNED = "запланировано"


def _date(base: dt.date, days_ago: int) -> str:
    return (base - dt.timedelta(days=days_ago)).strftime("%d.%m.%Y")


def _has_label(rec: UsvoRecord, needle: str) -> bool:
    needle = needle.lower()
    for f in (*rec.primary, *rec.secondary, *rec.extra):
        if needle in f.label.lower():
            return f.value
    return ""


def _health_issue(rec: UsvoRecord) -> bool:
    flags = rec.flags or {}
    txt = " ".join(f.value.lower() for f in rec.primary)
    return ("инвалид" in txt or "ампутац" in txt or "увеч" in txt or "тяжел" in txt)


def build_history(rec: UsvoRecord, real_appeals: list[dict] | None = None) -> list[dict]:
    """Лента событий взаимодействия (новые сверху)."""
    rng = random.Random(rec.id * 7919 + 13)
    today = msk_today()
    flags = rec.flags or {}
    events: list[dict] = []

    def add(days_ago: int, kind: str, title: str, detail: str, org: str, status: str):
        events.append({
            "_days": days_ago,
            "date": _date(today, days_ago),
            "kind": kind,
            "title": title,
            "detail": detail,
            "org": org,
            "status": status,
        })

    # 1) Постановка на учёт — самое старое событие.
    add(rng.randint(420, 540), "status",
        "Постановка на учёт",
        "Участник СВО внесён в реестр единого центра поддержки. Заведена "
        "персональная карточка, назначен куратор от администрации.",
        ORGS["admin"], DONE)

    # 2) Первичный обзвон / актуализация данных.
    add(rng.randint(300, 400), "contact",
        "Первичный обзвон",
        "Проведён вводный телефонный опрос: уточнены контакты, состав семьи и "
        "первоочередные потребности.",
        ORGS["admin"], DONE)

    # 3) Оформление статуса ВБД.
    if flags.get("vbd"):
        add(rng.randint(230, 300), "status",
            "Выдано удостоверение «Ветеран боевых действий»",
            "Оформлен статус ВБД, разъяснён перечень федеральных и региональных "
            "льгот, положенных ветерану.",
            ORGS["voenkomat"], DONE)
    else:
        add(rng.randint(20, 60), "status",
            "Оформление удостоверения ВБД",
            "Поданы документы на присвоение статуса «Ветеран боевых действий». "
            "Ожидается решение комиссии.",
            ORGS["voenkomat"], PROGRESS)

    # 4) Визит в МФЦ за справками/выплатами.
    add(rng.randint(150, 230), "doc",
        "Оформление единовременной выплаты",
        "Поданы документы на региональную единовременную выплату участнику СВО. "
        "Заявление принято, проверка документов завершена.",
        ORGS["mfc"], DONE)

    # 5) Здоровье/реабилитация.
    if _health_issue(rec):
        add(rng.randint(90, 160), "med",
            "Медицинская реабилитация",
            "Пройден курс восстановительного лечения. Оформлено направление на "
            "технические средства реабилитации (ТСР).",
            ORGS["gospital"], DONE)
        add(rng.randint(10, 40), "med",
            "Путёвка на санаторно-курортное лечение",
            "Подобрана и согласована путёвка по профилю заболевания.",
            ORGS["sanatoriy"], PLANNED)
    else:
        add(rng.randint(90, 160), "med",
            "Диспансеризация",
            "Пройден плановый медицинский осмотр, противопоказаний не выявлено.",
            ORGS["poliklinika"], DONE)

    # 6) Трудоустройство / переобучение.
    if flags.get("unemployed"):
        add(rng.randint(40, 90), "work",
            "Подбор вакансий",
            "Гражданин обратился за содействием в трудоустройстве. Подобраны "
            "вакансии, оформлено направление к работодателю.",
            ORGS["zanyatost"], PROGRESS)
        if _has_label(rec, "дополнительное образование"):
            add(rng.randint(15, 45), "edu",
                "Запись на профобучение",
                "Согласовано бесплатное переобучение по востребованной "
                "специальности в рамках программы поддержки занятости.",
                ORGS["uchebcentr"], PLANNED)
    else:
        add(rng.randint(40, 90), "work",
            "Трудоустройство",
            "При содействии центра занятости заключён трудовой договор. "
            "Гражданин приступил к работе.",
            ORGS["zanyatost"], DONE)

    # 7) Вовлечение в ветеранские организации.
    if flags.get("org_vremya") or flags.get("org_geroi_mo") or flags.get("org_assoc"):
        add(rng.randint(30, 80), "org",
            "Участие в ветеранской организации",
            "Включён в программу наставничества и общественные мероприятия "
            "сообщества ветеранов СВО.",
            ORGS["fond"], DONE)
    else:
        add(rng.randint(5, 25), "org",
            "Приглашение в сообщество ветеранов",
            "Направлено приглашение вступить в Ассоциацию ветеранов СВО и "
            "программу «Время Героя».",
            ORGS["fond"], PLANNED)

    # 8) Личный приём в администрации.
    add(rng.randint(8, 30), "visit",
        "Личный приём в администрации",
        "Очный приём у куратора: сверены меры поддержки, обновлены контактные "
        "данные, даны разъяснения по льготам ЖКХ.",
        ORGS["admin"], DONE)

    # Вливаем реальные обращения как события.
    for a in (real_appeals or []):
        days = 0
        ts = a.get("created_at") or 0
        if ts:
            d = msk_datetime(ts).date()
            days = (today - d).days
        events.append({
            "_days": days,
            "date": a.get("created_human") or _date(today, days),
            "kind": "appeal",
            "title": "Обращение в контакт-центр",
            "detail": a.get("question") or "",
            "org": "Бот MAX · Единый центр поддержки",
            "status": DONE if a.get("status") == "answered" else PROGRESS,
        })

    events.sort(key=lambda e: e["_days"])
    for e in events:
        e.pop("_days", None)
    return events
