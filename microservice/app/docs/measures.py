"""Меры поддержки: тонкий слой над Store (без хардкода мер).

Меры заводит администратор в веб-кабинете; здесь — только разбор JSON-структуры
строки `support_measures`, формирование текста для базы знаний и детерминированный
офлайн-подбор меры по запросу (фолбэк, когда Dify-ассистент не настроен).
"""
from __future__ import annotations

import json
import re


def _load_data(raw: str | None) -> dict:
    try:
        data = json.loads(raw or "{}")
        return data if isinstance(data, dict) else {}
    except (ValueError, TypeError):
        return {}


def humanize_key(key: str) -> str:
    """`military_office_certificate_date` → «Military office certificate date»-подобную
    человекочитаемую подпись на основе ключа (используется как дефолтный label)."""
    return (key or "").replace("_", " ").strip().capitalize()


def normalize_documents(documents) -> list[dict]:
    """Приводит список документов к [{title, sort_order}] (пустые отбрасываются)."""
    out: list[dict] = []
    for i, doc in enumerate(documents or []):
        if isinstance(doc, dict):
            title = str(doc.get("title", "")).strip()
        else:
            title = str(doc or "").strip()
        if title:
            out.append({"title": title, "sort_order": i})
    return out


def normalize_placeholders(placeholders) -> list[dict]:
    """Приводит плейсхолдеры к [{key, label}] (ключ обязателен и уникален)."""
    out: list[dict] = []
    seen: set[str] = set()
    for ph in placeholders or []:
        if isinstance(ph, dict):
            key = str(ph.get("key", "")).strip()
            label = str(ph.get("label", "")).strip()
        else:
            key = str(ph or "").strip()
            label = ""
        key = re.sub(r"[^A-Za-z0-9_]", "", key)
        if not key or key in seen:
            continue
        seen.add(key)
        out.append({"key": key, "label": label or humanize_key(key)})
    return out


def build_measure_data(documents, placeholders, llm_hint: str = "", category: str = "") -> dict:
    """Собирает JSON-структуру для колонки support_measures.data."""
    return {
        "documents": normalize_documents(documents),
        "placeholders": normalize_placeholders(placeholders),
        "llm_hint": (llm_hint or "").strip(),
        "category": (category or "").strip(),
    }


def measure_to_dict(row) -> dict:
    """Строка support_measures → словарь для API и логики бота."""
    data = _load_data(row["data"] if "data" in row.keys() else None)
    template_path = (row["template_path"] or "") if "template_path" in row.keys() else ""
    return {
        "id": int(row["id"]),
        "title": row["title"] or "",
        "description": row["description"] or "",
        "documents": normalize_documents(data.get("documents")),
        "placeholders": normalize_placeholders(data.get("placeholders")),
        "llm_hint": data.get("llm_hint", "") or "",
        "category": data.get("category", "") or "",
        "active": bool(row["active"]),
        "has_template": bool(template_path),
        "template_path": template_path,
        "created_at": row["created_at"] if "created_at" in row.keys() else None,
        "updated_at": row["updated_at"] if "updated_at" in row.keys() else None,
    }


def all_measures(store) -> list[dict]:
    return [measure_to_dict(r) for r in store.list_support_measures()]


def active_measures(store) -> list[dict]:
    return [measure_to_dict(r) for r in store.list_support_measures(active_only=True)]


def placeholder_fields(measure: dict) -> list[dict]:
    """Список {key, label} для распознавания визуальной моделью."""
    return [{"key": p["key"], "label": p["label"]} for p in measure.get("placeholders", [])]


def one_measure_text(m: dict) -> str:
    """Самодостаточное описание одной меры для отдельного документа базы знаний."""
    docs = ", ".join(d["title"] for d in m["documents"]) or "—"
    lines = [f"Мера поддержки (id {m['id']}): {m['title']}"]
    if m.get("description"):
        lines.append(f"Описание: {m['description']}")
    if m.get("llm_hint"):
        lines.append(f"Когда подходит: {m['llm_hint']}")
    lines.append(f"Необходимые документы: {docs}")
    return "\n".join(lines)


def measure_kb_text(measures: list[dict]) -> str:
    """Человекочитаемое описание всех мер для базы знаний (источник для LLM)."""
    blocks: list[str] = ["Доступные меры поддержки участников СВО и их семей:", ""]
    for m in measures:
        blocks.append(one_measure_text(m))
        blocks.append("")
    return "\n".join(blocks).strip()


_WORD = re.compile(r"[А-Яа-яЁёA-Za-z]{4,}")


def _significant_words(*texts: str) -> set[str]:
    words: set[str] = set()
    for t in texts:
        for w in _WORD.findall((t or "").lower()):
            words.add(w[:6])  # грубая нормализация окончаний (как корни в topics.py)
    return words


# Демонстрационные меры поддержки семей участников СВО (сценарий из макета).
# Заводятся один раз при старте, если мер с такими названиями ещё нет; дальше их
# можно редактировать в веб-кабинете как любые другие (см. seed_demo_measures).
DEMO_MEASURES: list[dict] = [
    {
        "title": "Бесплатное горячее питание для детей в школе",
        "description": (
            "Региональная мера для детей участников СВО: бесплатное горячее питание "
            "в общеобразовательной организации Московской области."
        ),
        "category": "Меры поддержки семей участников СВО",
        "llm_hint": "школа, питание, дети, ребёнок в школе, супруга, член семьи участника СВО",
        "documents": ["Паспорт (главный разворот)", "Свидетельство о заключении брака"],
        "placeholders": [
            {"key": "applicant_full_name", "label": "ФИО заявителя"},
            {"key": "passport_series", "label": "Серия паспорта"},
            {"key": "passport_number", "label": "Номер паспорта"},
            {"key": "participant_full_name", "label": "ФИО участника СВО"},
            {"key": "child_full_name", "label": "ФИО ребёнка"},
            {"key": "school_number", "label": "Номер школы"},
            {"key": "class_name", "label": "Класс"},
        ],
    },
    {
        "title": "Полное освобождение от платы за детский сад",
        "description": (
            "Региональная мера для детей участников СВО: полное освобождение от платы "
            "за присмотр и уход в дошкольной образовательной организации."
        ),
        "category": "Меры поддержки семей участников СВО",
        "llm_hint": "детский сад, дошкольное, плата за садик, ребёнок, супруга, член семьи участника СВО",
        "documents": ["Паспорт (главный разворот)", "Свидетельство о рождении ребёнка"],
        "placeholders": [
            {"key": "applicant_full_name", "label": "ФИО заявителя"},
            {"key": "passport_series", "label": "Серия паспорта"},
            {"key": "passport_number", "label": "Номер паспорта"},
            {"key": "child_full_name", "label": "ФИО ребёнка"},
            {"key": "child_birth_date", "label": "Дата рождения ребёнка"},
            {"key": "kindergarten_number", "label": "Номер/название детского сада"},
        ],
    },
]


def seed_demo_measures(store) -> int:
    """Идемпотентно заводит меры из DEMO_MEASURES, которых ещё нет в БД.

    Нужно, чтобы сценарий «Меры поддержки» из макета работал сразу «из коробки»:
    подобранные меры и запрашиваемые документы — настоящие записи (их видно и в
    веб-кабинете, они редактируемы). Сверка по названию — повторный старт дублей
    не создаёт. Возвращает число добавленных мер.
    """
    existing = {(r["title"] or "").strip().lower() for r in store.list_support_measures()}
    added = 0
    for m in DEMO_MEASURES:
        if m["title"].strip().lower() in existing:
            continue
        data = build_measure_data(
            m["documents"], m["placeholders"],
            llm_hint=m.get("llm_hint", ""), category=m.get("category", ""),
        )
        store.add_support_measure({
            "title": m["title"],
            "description": m.get("description", ""),
            "data": json.dumps(data, ensure_ascii=False),
            "template_path": "",
            "active": True,
        })
        added += 1
    return added


def match_measure_offline(query: str, measures: list[dict]) -> dict | None:
    """Детерминированный подбор меры по совпадению значимых слов запроса.

    Возвращает {measure_id, measure_title, found, confidence} либо None, если
    активных мер нет. found=False, когда совпадений не нашлось.
    """
    if not measures:
        return None
    q_words = _significant_words(query)
    best: dict | None = None
    best_hits = 0
    for m in measures:
        m_words = _significant_words(m["title"], m["description"], m["llm_hint"])
        hits = len(q_words & m_words)
        if hits > best_hits:
            best_hits = hits
            best = m
    if not best or best_hits == 0:
        return {"measure_id": None, "measure_title": "", "found": False, "confidence": 0.0}
    return {
        "measure_id": best["id"],
        "measure_title": best["title"],
        "found": True,
        "confidence": min(1.0, 0.4 + 0.2 * best_hits),
    }
