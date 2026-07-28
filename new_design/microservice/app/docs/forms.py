"""Описание заявления и сборка заполненного .docx."""
from __future__ import annotations

import datetime as dt

from app.common.docx import Paragraph, build_docx

# Мера поддержки, которую оформляет бот по фотографиям документов.
DEFAULT_MEASURE = {
    "key": "zhku_compensation",
    "title": "Ежемесячная денежная компенсация расходов на оплату ЖКУ",
    "category": "Ветеран боевых действий (участник СВО)",
    "category_code": "061",
    "form_title": (
        "Заявление о назначении ежемесячной денежной компенсации расходов по "
        "оплате жилого помещения и коммунальных услуг отдельным категориям граждан, "
        "имеющим место жительства в Московской области"
    ),
    "department": (
        "Министерство социального развития Московской области, "
        "Ленинское управление социальной защиты населения"
    ),
}

_BLANK = "____________________"


def _g(d: dict, key: str, default: str = "") -> str:
    v = d.get(key)
    return str(v).strip() if v not in (None, "") else default


def _cap(s: str) -> str:
    s = (s or "").strip()
    return s[:1].upper() + s[1:] if s else ""


def normalize_application(raw: dict | None) -> dict:
    """Приводит данные заявления к единой структуре с безопасными дефолтами."""
    raw = raw or {}
    applicant = raw.get("applicant") or {}
    representative = raw.get("representative") or {}
    family = raw.get("family") or []
    providers = raw.get("providers") or []
    payment = raw.get("payment") or {}

    return {
        "measure_key": _g(raw, "measure_key", DEFAULT_MEASURE["key"]),
        "measure_title": _g(raw, "measure_title", DEFAULT_MEASURE["title"]),
        "category": _g(raw, "category", DEFAULT_MEASURE["category"]),
        "category_code": _g(raw, "category_code", DEFAULT_MEASURE["category_code"]),
        "department": _g(raw, "department", DEFAULT_MEASURE["department"]),
        "applicant": {
            "fio": _g(applicant, "fio"),
            "birth_date": _g(applicant, "birth_date"),
            "passport_series": _g(applicant, "passport_series"),
            "passport_number": _g(applicant, "passport_number"),
            "passport_issued": _g(applicant, "passport_issued"),
            "address": _g(applicant, "address"),
            "phone": _g(applicant, "phone"),
            "phone_home": _g(applicant, "phone_home"),
            "email": _g(applicant, "email"),
        },
        # Представитель заявителя (если оформляет не сам гражданин) — обычно пуст.
        "representative": {
            "fio": _g(representative, "fio"),
            "passport_series": _g(representative, "passport_series"),
            "passport_number": _g(representative, "passport_number"),
            "passport_issued": _g(representative, "passport_issued"),
            "authority": _g(representative, "authority"),
        },
        "ownership": _cap(_g(raw, "ownership", "Частное")),
        "rooms": _g(raw, "rooms"),
        "family": [
            {"fio": _g(m, "fio"), "birth_date": _g(m, "birth_date"),
             "relation": _cap(_g(m, "relation"))}
            for m in family if isinstance(m, dict) and (m.get("fio") or m.get("relation"))
        ],
        "providers": [
            {"name": _g(p, "name"), "account": _g(p, "account")}
            for p in providers if isinstance(p, dict) and (p.get("name") or p.get("account"))
        ],
        "payment": {
            "method": _g(payment, "method", "bank"),  # bank | post
            "bank": _g(payment, "bank"),
            "account": _g(payment, "account"),
        },
        "missing": list(raw.get("missing") or []),
        "confidence": raw.get("confidence", 0.0),
        "summary_note": _g(raw, "summary_note"),
    }


def application_summary(app: dict) -> str:
    """Короткое описание заявления для сообщения в MAX и карточки в админке."""
    a = app["applicant"]
    lines = [
        f"📄 {app['measure_title']}",
        "",
        f"Заявитель: {a['fio'] or '—'}",
        f"Дата рождения: {a['birth_date'] or '—'}",
        f"Адрес: {a['address'] or '—'}",
        f"Категория льготы: {app['category']} (код {app['category_code']})",
    ]
    if a["passport_series"] or a["passport_number"]:
        lines.append(
            f"Паспорт: серия {a['passport_series'] or '—'} № {a['passport_number'] or '—'}"
        )
    if app["providers"]:
        provs = "; ".join(
            f"{p['name']} (л/с {p['account'] or '—'})" for p in app["providers"]
        )
        lines.append(f"Поставщики ЖКУ: {provs}")
    if app["missing"]:
        lines.append("")
        lines.append("⚠️ Нужно уточнить: " + ", ".join(app["missing"]))
    return "\n".join(lines)


def build_application_docx(app: dict) -> bytes:
    """Рендерит заполненное заявление в .docx."""
    a = app["applicant"]
    today = dt.date.today().strftime("%d.%m.%Y")
    P = Paragraph
    paras: list[Paragraph] = [
        P("Форма заявления о предоставлении государственной услуги", size=10,
          align="right"),
        P(""),
        P(DEFAULT_MEASURE["form_title"], bold=True, align="center"),
        P(""),
        P(f"В {app['department'] or _BLANK}"),
        P("(наименование территориального структурного подразделения)", size=9),
        P(""),
        P(f"Заявитель: {a['fio'] or _BLANK}, дата рождения {a['birth_date'] or _BLANK}"),
        P(
            "Документ, удостоверяющий личность Заявителя: серия "
            f"{a['passport_series'] or _BLANK} № {a['passport_number'] or _BLANK}, "
            f"выдан: {a['passport_issued'] or _BLANK}"
        ),
        P(f"Адрес места жительства Заявителя: {a['address'] or _BLANK}"),
    ]

    rep = app.get("representative") or {}
    if rep.get("fio"):
        paras += [
            P(f"Представитель заявителя: {rep['fio']}"),
            P(
                "Документ, удостоверяющий личность Представителя: серия "
                f"{rep.get('passport_series') or _BLANK} № {rep.get('passport_number') or _BLANK}, "
                f"выдан: {rep.get('passport_issued') or _BLANK}"
            ),
            P(f"Документ, подтверждающий полномочия Представителя: {rep.get('authority') or _BLANK}"),
        ]

    paras += [
        P(""),
        P(f"Фонд собственности жилого помещения: {app['ownership'] or _BLANK}"),
        P(f"Количество комнат в жилом помещении: {app['rooms'] or _BLANK}"),
        P(""),
        P("Прошу назначить компенсацию (одно основание по выбору гражданина):", bold=True),
        P(f"— по оплате жилого помещения: код {app['category_code'] or _BLANK} — "
          f"{app['category'] or _BLANK}, дата начала: {today};"),
        P("— по оплате взносов на капитальный ремонт: —;"),
        P(f"— по коммунальным услугам: код {app['category_code'] or _BLANK} — "
          f"{app['category'] or _BLANK}, дата начала: {today}."),
        P(""),
    ]

    if app["family"]:
        paras.append(P("Члены семьи, пользующиеся льготой:", bold=True))
        for m in app["family"]:
            paras.append(
                P(f"  • {m['fio'] or _BLANK}, {m['birth_date'] or '—'} — {m['relation'] or '—'}")
            )
        paras.append(P(""))

    if app["providers"]:
        paras.append(P("Организации — поставщики жилищно-коммунальных услуг:", bold=True))
        for i, p in enumerate(app["providers"], 1):
            paras.append(P(f"  {i}. {p['name'] or _BLANK} — лицевой счёт {p['account'] or _BLANK}"))
        paras.append(P(""))

    pay = app["payment"]
    if pay["method"] == "post":
        paras.append(P(f"Выплачивать компенсацию: Почтой по адресу {a['address'] or _BLANK}."))
    else:
        paras.append(
            P(f"Выплачивать компенсацию: Банк {pay['bank'] or _BLANK}, счёт "
              f"{pay['account'] or _BLANK}.")
        )

    paras += [
        P(""),
        P(
            "Обязуюсь сообщать об обстоятельствах, влекущих прекращение или "
            "приостановление начислений выплаты (изменение состава семьи, места "
            "жительства, лицевого счёта, паспортных данных и др.), в течение 1 месяца.",
            size=9,
        ),
        P(
            "О ходе рассмотрения уведомлять через Личный кабинет на РПГУ "
            "uslugi.mosreg.ru и по электронной почте.", size=9,
        ),
        P(""),
        P(f"Телефон: дом. {a.get('phone_home') or _BLANK}    моб. {a['phone'] or _BLANK}"),
        P(f"Адрес электронной почты: {a['email'] or _BLANK}"),
        P(""),
        P(f"Подпись заявителя ________________  /{a['fio'] or _BLANK}/    Дата: {today}"),
        P(""),
        P("Заявление сформировано в контакт-центре поддержки участников СВО по "
          "фотографиям документов и подтверждено заявителем.", size=8),
    ]
    return build_docx(paras)
