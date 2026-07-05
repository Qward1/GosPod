"""Производный SQLite-индекс карточек УСВО для точного Text-to-SQL поиска.

Зачем
-----
Карточек ~1500. Отдавать их все в контекст модели нельзя (лимит 32k токенов, цена,
задержка), а RAG не умеет отвечать на точные/составные вопросы («сколько с >3 детьми»,
«все с инвалидностью 2 группы и двумя детьми») — он ищет похожие чанки, а не перебирает
базу. Поэтому фильтрацию и подсчёты выполняем в обычной БД, а LLM используем только для
понимания вопроса (вопрос → JSON-фильтр) и для формулировки итогового ответа.

Что здесь
---------
- Схема + сборка нормализованной проекции карточек в SQLite (`usvo_index` + EAV `usvo_attr`).
  Источник истины (карточки/импорт/редактирование) НЕ трогаем — индекс полностью производный
  и пересобирается при изменении набора карточек.
- `FIELDS` — whitelist полей и их типов (единственные допустимые имена колонок в SQL).
- `validate_filter` — проверяет JSON-фильтр (поля/операторы/типы/глубину/лимиты), отбрасывая
  небезопасное; SQL строится ТОЛЬКО параметрически.
- `UsvoIndex.run` — валидирует фильтр и исполняет его в SQLite, возвращает id карточек,
  общее число и (для агрегатов) разбивку.

Нормализаторы канонических полей переиспользуют логику из `usvo_knowledge`
(`_age`, `_locality`, `_yes`, `_field_value`, `_record_fields`) и флаги `record.flags`.
"""
from __future__ import annotations

import datetime as dt
import os
import re
import sqlite3
import threading
from collections.abc import Callable
from contextlib import contextmanager

from app.web.usvo import UsvoRecord
from app.web.usvo_knowledge import (
    _age,
    _field_value,
    _locality,
    _record_fields,
    _yes,
)

# --------------------------------------------------------------------------- #
# Схема индекса
# --------------------------------------------------------------------------- #

_SCHEMA = """
CREATE TABLE IF NOT EXISTS usvo_index (
    card_id           INTEGER PRIMARY KEY,
    name              TEXT,
    status            TEXT,
    gender            TEXT,
    birth_date        TEXT,
    age               INTEGER,
    locality          TEXT,
    region            TEXT,
    address           TEXT,
    marital           TEXT,
    children_count    INTEGER,
    minor_children    INTEGER,
    disabled_children INTEGER,
    disability        INTEGER,
    disability_group  INTEGER,
    vbd               INTEGER,
    unemployed        INTEGER,
    needs_support     INTEGER,
    support_need      TEXT,
    awards_count      INTEGER,
    has_awards        INTEGER,
    awards            TEXT,
    org_vremya        INTEGER,
    org_geroi_mo      INTEGER,
    org_assoc         INTEGER,
    in_org            INTEGER,
    head_directive    INTEGER,
    stale_contact     INTEGER,
    education         TEXT,
    profession        TEXT,
    phone             TEXT,
    source            TEXT,
    search_text       TEXT
);

CREATE TABLE IF NOT EXISTS usvo_attr (
    card_id INTEGER NOT NULL,
    key     TEXT NOT NULL,
    label   TEXT,
    value   TEXT,
    num     REAL,
    bool    INTEGER,
    PRIMARY KEY (card_id, key)
);

CREATE INDEX IF NOT EXISTS idx_usvo_index_status   ON usvo_index(status);
CREATE INDEX IF NOT EXISTS idx_usvo_index_locality ON usvo_index(locality);
CREATE INDEX IF NOT EXISTS idx_usvo_index_age      ON usvo_index(age);
CREATE INDEX IF NOT EXISTS idx_usvo_attr_key       ON usvo_attr(key);
"""

# Порядок колонок для INSERT (= порядок в _row_for_record).
_COLUMNS = [
    "card_id", "name", "status", "gender", "birth_date", "age", "locality", "region",
    "address", "marital", "children_count", "minor_children", "disabled_children",
    "disability", "disability_group", "vbd", "unemployed", "needs_support",
    "support_need", "awards_count", "has_awards", "awards", "org_vremya",
    "org_geroi_mo", "org_assoc", "in_org", "head_directive", "stale_contact",
    "education", "profession", "phone", "source", "search_text",
]


# --------------------------------------------------------------------------- #
# Whitelist полей для фильтра (единственные допустимые имена колонок)
# --------------------------------------------------------------------------- #
# type: "text" | "int" | "bool" | "enum"
FIELDS: dict[str, dict] = {
    "name":              {"col": "name", "type": "text"},
    "status":            {"col": "status", "type": "text"},
    "gender":            {"col": "gender", "type": "enum"},
    "age":               {"col": "age", "type": "int"},
    "birth_date":        {"col": "birth_date", "type": "text"},
    "locality":          {"col": "locality", "type": "text"},
    "region":            {"col": "region", "type": "text"},
    "address":           {"col": "address", "type": "text"},
    "marital":           {"col": "marital", "type": "text"},
    "children_count":    {"col": "children_count", "type": "int"},
    "minor_children":    {"col": "minor_children", "type": "int"},
    "disabled_children": {"col": "disabled_children", "type": "int"},
    "disability":        {"col": "disability", "type": "bool"},
    "disability_group":  {"col": "disability_group", "type": "int"},
    "vbd":               {"col": "vbd", "type": "bool"},
    "unemployed":        {"col": "unemployed", "type": "bool"},
    "needs_support":     {"col": "needs_support", "type": "bool"},
    "support_need":      {"col": "support_need", "type": "text"},
    "awards_count":      {"col": "awards_count", "type": "int"},
    "has_awards":        {"col": "has_awards", "type": "bool"},
    "awards":            {"col": "awards", "type": "text"},
    "org_vremya":        {"col": "org_vremya", "type": "bool"},
    "org_geroi_mo":      {"col": "org_geroi_mo", "type": "bool"},
    "org_assoc":         {"col": "org_assoc", "type": "bool"},
    "in_org":            {"col": "in_org", "type": "bool"},
    "head_directive":    {"col": "head_directive", "type": "bool"},
    "stale_contact":     {"col": "stale_contact", "type": "bool"},
    "education":         {"col": "education", "type": "text"},
    "profession":        {"col": "profession", "type": "text"},
    "phone":             {"col": "phone", "type": "text"},
    "source":            {"col": "source", "type": "text"},
}

# Разрешённые операторы по типу поля.
_TEXT_CMP = {"=", "!=", "contains", "in", "exists"}
_INT_CMP = {"=", "!=", ">", ">=", "<", "<=", "between", "in", "exists"}
_BOOL_CMP = {"=", "exists"}
_CMP_BY_TYPE = {"text": _TEXT_CMP, "enum": _TEXT_CMP, "int": _INT_CMP, "bool": _BOOL_CMP}
# Для произвольных полей (attr:<key>) — без "!=" (семантика NOT EXISTS неоднозначна).
_ATTR_CMP = {"=", "contains", ">", ">=", "<", "<=", "between", "exists"}
_NUMERIC_OPS = {">", ">=", "<", "<="}

INTENTS = {"count", "list", "aggregate", "lookup"}
ORDER_FIELDS = {"age", "name", "children_count", "awards_count", "disability_group",
                "status", "locality"}
AGG_FIELDS = {"status", "locality", "gender", "age_bucket", "disability",
              "disability_group", "children_count", "has_awards", "in_org", "vbd",
              "marital", "education", "source", "needs_support", "unemployed"}

MAX_DEPTH = 3
MAX_CONDITIONS = 12
MAX_LIMIT = 200
DEFAULT_LIMIT = 200


# --------------------------------------------------------------------------- #
# Нормализаторы (вопрос → типизированные значения карточки)
# --------------------------------------------------------------------------- #

_WORD_NUM = {
    "один": 1, "одного": 1, "одна": 1, "ноль": 0,
    "два": 2, "две": 2, "двое": 2, "двух": 2, "пара": 2,
    "три": 3, "трое": 3, "трёх": 3, "трех": 3,
    "четыре": 4, "четверо": 4, "четырёх": 4, "четырех": 4,
    "пять": 5, "пятеро": 5, "пяти": 5,
    "шесть": 6, "шестеро": 6, "семь": 7, "восемь": 8,
}
# Именованные упоминания ребёнка (для подсчёта). Намеренно НЕ включает обобщённое
# «дети/детей», иначе «двое детей: сын …, дочь …» считалось бы как 3+ ребёнка.
_CHILD_NAMED = re.compile(r"\b(сын\w*|доч\w*|дочь|реб[ёе]н\w*)\b")
_CHILD_ANY = re.compile(r"дет(?:и|ей|ьми|ям|ей)?\b|реб[ёе]н|сын|доч")
_YEAR_RE = re.compile(r"\b(19\d{2}|20\d{2})\b")
_DISAB_GROUP_RE = re.compile(r"(\d)\s*-?\s*(?:й|я|ая|ой)?\s*групп", re.IGNORECASE)
_DISAB_GROUP_RE2 = re.compile(r"групп\w*\s*[:\-]?\s*(\d)", re.IGNORECASE)
_ROMAN = {"i": 1, "ii": 2, "iii": 3}


def _norm_text(value: str) -> str:
    return " ".join((value or "").replace("ё", "е").lower().split())


def parse_children(value: str) -> tuple[int, int]:
    """«сын, 2014 г.р.» / «двое детей» / «нет» → (всего детей, несовершеннолетних).

    Несовершеннолетние считаются по годам рождения «… г.р.» (возраст < 18 на сегодня).
    Если детей явно «нет» — (0, 0). Если есть упоминание детей, но число не извлекается —
    считаем 1 ребёнка.
    """
    s = _norm_text(value)
    if not s or s in {"-", "—", "0"} or s.startswith("нет") or s.startswith("без дет") \
            or "детей нет" in s or "бездетн" in s:
        return (0, 0)

    explicit = 0
    m = re.search(r"(\d+)\s*(?:детей|реб[ёе]н|дет\b|ребят|ребенк|ребёнк)", s)
    if m:
        explicit = int(m.group(1))
    word = 0
    for w, num in _WORD_NUM.items():
        if re.search(rf"\b{w}\b", s):
            word = max(word, num)
    years = [int(y) for y in _YEAR_RE.findall(s)]
    named = len(_CHILD_NAMED.findall(s))

    count = max(explicit, word, len(years), named)
    if not count and _CHILD_ANY.search(s):
        count = 1  # есть упоминание ребёнка, но число не извлекается

    today = dt.date.today()
    minors = sum(1 for y in years if 0 <= today.year - y < 18)
    if minors > count:
        minors = count
    return (count, minors)


def parse_disability(record: UsvoRecord) -> tuple[int, int | None]:
    """Инвалидность участника и её группа (1..3) из полей примечания/здоровья/статуса.

    Поля про детей-инвалидов («дети с ограниченными возможностями») сюда не входят.
    Возвращает (0|1, group|None).
    """
    candidates: list[str] = []
    for label, value in _record_fields(record):
        ll = _norm_text(label)
        if any(t in ll for t in ("дет", "ребен", "ограничен")):
            continue
        if any(t in ll for t in ("инвалид", "примечан", "состоян", "здоров", "группа")):
            candidates.append(value)
    if record.status:
        candidates.append(record.status)

    disabled = 0
    group: int | None = None
    for raw in candidates:
        v = _norm_text(raw)
        if "инвалид" not in v and "групп" not in v:
            continue
        # Явное отрицание («инвалидности нет», «без инвалидности»).
        if re.search(r"(?:без инвалид|инвалидност\w*\s+нет|нет инвалид)", v):
            continue
        disabled = 1
        m = _DISAB_GROUP_RE.search(v) or _DISAB_GROUP_RE2.search(v)
        if m:
            g = int(m.group(1))
            if 1 <= g <= 3:
                group = g
        if group is None:
            rm = re.search(r"\b(iii|ii|i)\s*групп", v)
            if rm:
                group = _ROMAN.get(rm.group(1))
    return (disabled, group)


def parse_gender(record: UsvoRecord) -> str:
    """«м» | «ж» | «»: по явному полю «Пол», иначе по отчеству."""
    explicit = _field_value(record, ("пол участника", "пол ")).strip().lower()
    if explicit.startswith("м"):
        return "м"
    if explicit.startswith("ж"):
        return "ж"
    parts = [p for p in (record.name or "").split() if p]
    patronymic = parts[2].lower() if len(parts) >= 3 else ""
    if patronymic.endswith(("вна", "чна", "ична")):
        return "ж"
    if patronymic.endswith(("вич", "ьич", "ич")):
        return "м"
    return ""


def _has_awards(awards: str) -> bool:
    a = (awards or "").strip().lower()
    return bool(a) and "нет" not in a and a not in ("—", "-")


def _awards_count(awards: str) -> int:
    if not _has_awards(awards):
        return 0
    parts = [p.strip() for p in re.split(r"[;,]", awards) if p.strip()]
    return len(parts) or 1


def _region(address: str) -> str:
    for part in (address or "").split(","):
        p = part.strip()
        if any(t in p.lower() for t in ("област", "край", "республик", "г.о.", "округ")):
            return p
    return ""


def _education(record: UsvoRecord) -> str:
    for label, value in _record_fields(record):
        ll = _norm_text(label)
        if "образован" in ll and "дополнит" not in ll:
            return value
    return ""


def _profession(record: UsvoRecord) -> str:
    for label, value in _record_fields(record):
        ll = _norm_text(label)
        if "профес" in ll or "специальн" in ll:
            return value
    return ""


def _disabled_children(record: UsvoRecord) -> int:
    val = _field_value(record, ("ограниченными возможностями", "дети-инвалид", "ребенок-инвалид"))
    flag = _yes(val)
    if flag is True:
        cnt, _ = parse_children(val)
        return cnt or 1
    return 0


def _num_from(value: str):
    """Первое число в строке (для EAV usvo_attr.num). None, если числа нет."""
    m = re.search(r"-?\d+(?:[.,]\d+)?", value or "")
    if not m:
        return None
    try:
        return float(m.group(0).replace(",", "."))
    except ValueError:
        return None


def attr_key(label: str) -> str:
    """Нормализованный ключ поля для EAV/фильтра attr:<key>."""
    return _norm_text(label)


def _row_for_record(record: UsvoRecord) -> tuple:
    flags = record.flags or {}
    children, minors = parse_children(_field_value(record, ("дети",)) or "")
    disabled, group = parse_disability(record)
    org_v = bool(flags.get("org_vremya"))
    org_g = bool(flags.get("org_geroi_mo"))
    org_a = bool(flags.get("org_assoc"))
    fields_text = " ".join(f"{l} {v}" for l, v in _record_fields(record))
    search_text = _norm_text(
        " ".join([record.name or "", record.status or "", record.address or "",
                  record.awards or "", fields_text])
    )
    return (
        int(record.id),
        record.name or "",
        record.status or "",
        parse_gender(record),
        record.birth_date or "",
        _age(record.birth_date),
        _locality(record.address),
        _region(record.address),
        record.address or "",
        _field_value(record, ("семейное положение", "семейн")),
        children,
        minors,
        _disabled_children(record),
        disabled,
        group,
        1 if flags.get("vbd") else 0,
        1 if flags.get("unemployed") else 0,
        1 if (flags.get("support_need") or "").strip() else 0,
        (flags.get("support_need") or "").strip(),
        _awards_count(record.awards or ""),
        1 if _has_awards(record.awards or "") else 0,
        record.awards or "",
        1 if org_v else 0,
        1 if org_g else 0,
        1 if org_a else 0,
        1 if (org_v or org_g or org_a) else 0,
        1 if record.head_directive else 0,
        1 if flags.get("stale_contact") else 0,
        _education(record),
        _profession(record),
        record.phone or "",
        record.source or "table",
        search_text,
    )


def _attr_rows(record: UsvoRecord) -> list[tuple]:
    out: list[tuple] = []
    seen: set[str] = set()
    for label, value in _record_fields(record):
        key = attr_key(label)
        if not key or key in seen:
            continue
        seen.add(key)
        flag = _yes(value)
        out.append((
            int(record.id), key, label, value, _num_from(value),
            None if flag is None else (1 if flag else 0),
        ))
    return out


# --------------------------------------------------------------------------- #
# Валидация фильтра (безопасность + корректность)
# --------------------------------------------------------------------------- #

def _coerce_number(value):
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return value
    if isinstance(value, str):
        m = re.search(r"-?\d+(?:[.,]\d+)?", value)
        if m:
            try:
                num = float(m.group(0).replace(",", "."))
                return int(num) if num.is_integer() else num
            except ValueError:
                return None
    return None


def _coerce_bool(value):
    if isinstance(value, bool):
        return value
    s = str(value).strip().lower()
    if s in {"1", "true", "yes", "да", "y", "истина"}:
        return True
    if s in {"0", "false", "no", "нет", "n", "ложь"}:
        return False
    return None


def _sanitize_attr_key(field: str) -> str:
    raw = field.split(":", 1)[1] if ":" in field else ""
    cleaned = re.sub(r"[^0-9a-zа-яё \-]", "", raw.replace("ё", "е").lower()).strip()
    return " ".join(cleaned.split())


def _validate_condition(raw: dict, errors: list[str]) -> dict | None:
    field = str(raw.get("field") or "").strip()
    cmp = str(raw.get("cmp") or "").strip().lower()
    value = raw.get("value")
    if not field or not cmp:
        errors.append("условие без field/cmp отброшено")
        return None

    # Произвольное поле карточки.
    if field.lower().startswith("attr:"):
        key = _sanitize_attr_key(field)
        if not key:
            errors.append("attr без ключа отброшено")
            return None
        if cmp not in _ATTR_CMP:
            errors.append(f"оператор {cmp!r} недопустим для attr")
            return None
        cond = {"kind": "attr", "field": f"attr:{key}", "attr_key": key, "cmp": cmp}
        return _coerce_value(cond, "attr", cmp, value, errors)

    spec = FIELDS.get(field)
    if not spec:
        errors.append(f"неизвестное поле {field!r} отброшено")
        return None
    ftype = spec["type"]
    if cmp not in _CMP_BY_TYPE[ftype]:
        errors.append(f"оператор {cmp!r} недопустим для поля {field!r} ({ftype})")
        return None
    cond = {"kind": "col", "field": field, "col": spec["col"], "type": ftype, "cmp": cmp}
    return _coerce_value(cond, ftype, cmp, value, errors)


def _coerce_value(cond: dict, ftype: str, cmp: str, value, errors: list[str]) -> dict | None:
    if cmp == "exists":
        cond["value"] = None
        return cond
    if cmp == "between":
        if not isinstance(value, (list, tuple)) or len(value) != 2:
            errors.append("between требует [a, b]")
            return None
        lo, hi = _coerce_number(value[0]), _coerce_number(value[1])
        if lo is None or hi is None:
            errors.append("between с нечисловыми границами")
            return None
        cond["value"] = sorted([lo, hi])
        return cond
    if cmp == "in":
        items = value if isinstance(value, (list, tuple)) else [value]
        if ftype == "int" or (ftype == "attr" and all(_coerce_number(i) is not None for i in items)):
            coerced = [_coerce_number(i) for i in items]
            coerced = [c for c in coerced if c is not None]
        else:
            coerced = [str(i).strip() for i in items if str(i).strip()]
        if not coerced:
            errors.append("in с пустым списком")
            return None
        cond["value"] = coerced
        return cond

    # Скалярные операторы.
    if ftype == "bool":
        b = _coerce_bool(value)
        if b is None:
            b = True  # «disability =» без значения трактуем как «есть»
        cond["value"] = 1 if b else 0
        return cond
    if cmp in _NUMERIC_OPS or ftype == "int":
        num = _coerce_number(value)
        if num is None:
            errors.append(f"нечисловое значение для {cond.get('field')!r}")
            return None
        cond["value"] = num
        cond["numeric"] = True
        return cond
    # text / enum / attr contains|=
    b = _coerce_bool(value) if ftype == "attr" else None
    if ftype == "attr" and b is not None and cmp == "=":
        cond["value"] = 1 if b else 0
        cond["boolean"] = True
        return cond
    cond["value"] = str(value).strip()
    if cond["value"] == "":
        errors.append("пустое строковое значение")
        return None
    return cond


def _validate_group(raw: dict, depth: int, budget: list[int], errors: list[str]) -> dict | None:
    if depth > MAX_DEPTH:
        errors.append("превышена глубина вложенности")
        return None
    match = str(raw.get("match") or "all").strip().lower()
    if match not in {"all", "any"}:
        match = "all"
    conditions = raw.get("conditions")
    if not isinstance(conditions, list):
        return None
    clean: list[dict] = []
    for item in conditions:
        if not isinstance(item, dict):
            continue
        if budget[0] <= 0:
            errors.append("превышено число условий")
            break
        if "match" in item or "conditions" in item:
            sub = _validate_group(item, depth + 1, budget, errors)
            if sub and sub.get("conditions"):
                clean.append(sub)
            continue
        budget[0] -= 1
        cond = _validate_condition(item, errors)
        if cond:
            clean.append(cond)
    if not clean:
        return None
    return {"match": match, "conditions": clean}


def validate_filter(raw: dict) -> tuple[dict, list[str]]:
    """Проверяет JSON-фильтр и возвращает (безопасный_фильтр, список_замечаний).

    Невалидные условия отбрасываются (запрос деградирует, но не падает). Возвращённый
    фильтр гарантированно состоит только из whitelist-полей и допустимых операторов —
    SQL по нему строится исключительно через bind-параметры.
    """
    errors: list[str] = []
    raw = raw if isinstance(raw, dict) else {}

    # "semantic" — особый passthrough-сигнал «это не запрос к базе» (вопрос идёт в RAG).
    intent = str(raw.get("intent") or "").strip().lower()
    if intent != "semantic" and intent not in INTENTS:
        intent = "list"

    where_raw = raw.get("where")
    where = None
    if isinstance(where_raw, dict):
        where = _validate_group(where_raw, 1, [MAX_CONDITIONS], errors)
    elif isinstance(where_raw, list):  # допускаем голый список условий
        where = _validate_group({"match": "all", "conditions": where_raw}, 1,
                                [MAX_CONDITIONS], errors)

    aggregate_by = str(raw.get("aggregate_by") or "").strip().lower()
    if aggregate_by and aggregate_by not in AGG_FIELDS:
        errors.append(f"aggregate_by {aggregate_by!r} не поддержан")
        aggregate_by = ""
    if intent == "aggregate" and not aggregate_by:
        aggregate_by = "status"

    order_by = str(raw.get("order_by") or "").strip().lower()
    if order_by and order_by not in ORDER_FIELDS:
        order_by = ""
    order_dir = str(raw.get("order_dir") or "").strip().lower()
    order_dir = "asc" if order_dir == "asc" else ("desc" if order_dir == "desc" else "")

    limit = raw.get("limit")
    if isinstance(limit, (int, float)) and int(limit) > 0:
        limit = min(int(limit), MAX_LIMIT)
    else:
        limit = None

    clean = {
        "intent": intent,
        "where": where,
        "aggregate_by": aggregate_by,
        "order_by": order_by,
        "order_dir": order_dir,
        "limit": limit,
    }
    return clean, errors


# --------------------------------------------------------------------------- #
# Построение SQL (только параметрически)
# --------------------------------------------------------------------------- #

def _pyfold(value):
    """Регистронезависимая нормализация для SQL (кириллица + ASCII)."""
    return value.casefold() if isinstance(value, str) else value


def _compile_cond(cond: dict, params: list) -> str:
    cmp = cond["cmp"]
    if cond["kind"] == "attr":
        return _compile_attr(cond, params)

    col = cond["col"]
    ftype = cond["type"]
    if ftype == "bool":
        if cmp == "exists":
            return f"{col} = 1"
        params.append(cond["value"])
        return f"{col} = ?"
    if cmp == "exists":
        return f"({col} IS NOT NULL AND {col} <> '')" if ftype in ("text", "enum") \
            else f"{col} IS NOT NULL"
    if cmp == "between":
        params.extend(cond["value"])
        return f"({col} IS NOT NULL AND {col} BETWEEN ? AND ?)"
    if cmp == "in":
        placeholders = ", ".join("?" for _ in cond["value"])
        if ftype == "int":
            params.extend(cond["value"])
            return f"{col} IN ({placeholders})"
        params.extend(str(v).casefold() for v in cond["value"])
        return f"pyfold({col}) IN ({placeholders})"
    if ftype == "int" or cond.get("numeric"):
        params.append(cond["value"])
        if cmp == "!=":
            return f"({col} IS NULL OR {col} <> ?)"
        return f"({col} IS NOT NULL AND {col} {cmp} ?)"
    # text / enum scalar
    if cmp == "contains":
        params.append(str(cond["value"]).casefold())
        return f"instr(pyfold({col}), ?) > 0"
    if cmp == "!=":
        params.append(str(cond["value"]).casefold())
        return f"pyfold({col}) <> ?"
    params.append(str(cond["value"]).casefold())
    return f"pyfold({col}) = ?"


def _compile_attr(cond: dict, params: list) -> str:
    cmp = cond["cmp"]
    sub = ["a.card_id = usvo_index.card_id", "instr(pyfold(a.key), ?) > 0"]
    params.append(cond["attr_key"].casefold())
    if cmp == "exists":
        pass
    elif cmp == "between":
        sub.append("a.num IS NOT NULL AND a.num BETWEEN ? AND ?")
        params.extend(cond["value"])
    elif cmp in _NUMERIC_OPS or cond.get("numeric"):
        sub.append(f"a.num IS NOT NULL AND a.num {cmp} ?")
        params.append(cond["value"])
    elif cond.get("boolean"):
        sub.append("a.bool = ?")
        params.append(cond["value"])
    elif cmp == "=":
        sub.append("pyfold(a.value) = ?")
        params.append(str(cond["value"]).casefold())
    else:  # contains
        sub.append("instr(pyfold(a.value), ?) > 0")
        params.append(str(cond["value"]).casefold())
    return "EXISTS (SELECT 1 FROM usvo_attr a WHERE " + " AND ".join(sub) + ")"


def _compile_group(group: dict | None, params: list) -> str:
    if not group or not group.get("conditions"):
        return ""
    parts: list[str] = []
    for cond in group["conditions"]:
        if "match" in cond and "conditions" in cond:
            sub = _compile_group(cond, params)
            if sub:
                parts.append(f"({sub})")
        else:
            parts.append(_compile_cond(cond, params))
    if not parts:
        return ""
    joiner = " AND " if group.get("match") == "all" else " OR "
    return joiner.join(parts)


_AGG_EXPR = {
    "status": "COALESCE(NULLIF(TRIM(status), ''), 'не указан')",
    "locality": "COALESCE(NULLIF(TRIM(locality), ''), 'не указан')",
    "marital": "COALESCE(NULLIF(TRIM(marital), ''), 'не указано')",
    "education": "COALESCE(NULLIF(TRIM(education), ''), 'не указано')",
    "source": "CASE WHEN source = 'uploaded' THEN 'загруженные' ELSE 'основная таблица' END",
    "disability_group": "CASE WHEN disability_group IS NULL THEN 'не указана' "
                        "ELSE (disability_group || ' группа') END",
    "children_count": "CASE WHEN children_count IS NULL THEN 'не указано' "
                      "ELSE CAST(children_count AS TEXT) END",
    "gender": "CASE gender WHEN 'м' THEN 'мужчины' WHEN 'ж' THEN 'женщины' "
              "ELSE 'не указано' END",
    "disability": "CASE WHEN disability = 1 THEN 'с инвалидностью' "
                  "ELSE 'без инвалидности' END",
    "has_awards": "CASE WHEN has_awards = 1 THEN 'с наградами' ELSE 'без наград' END",
    "in_org": "CASE WHEN in_org = 1 THEN 'в организациях' ELSE 'вне организаций' END",
    "vbd": "CASE WHEN vbd = 1 THEN 'ветераны БД' ELSE 'не ветераны' END",
    "needs_support": "CASE WHEN needs_support = 1 THEN 'нужна поддержка' ELSE 'не указано' END",
    "unemployed": "CASE WHEN unemployed = 1 THEN 'нужно трудоустройство' ELSE 'трудоустроены/нет данных' END",
    "age_bucket": "CASE WHEN age IS NULL THEN 'не указан' WHEN age < 25 THEN 'до 25' "
                  "WHEN age < 35 THEN '25–34' WHEN age < 45 THEN '35–44' "
                  "WHEN age < 55 THEN '45–54' ELSE '55 и старше' END",
}


def _order_clause(clean: dict) -> str:
    field = clean.get("order_by")
    if not field:
        return ""
    direction = "ASC" if clean.get("order_dir") == "asc" else "DESC"
    if not clean.get("order_dir"):
        direction = "ASC" if field == "name" else "DESC"
    col = field  # уже из ORDER_FIELDS
    return f" ORDER BY ({col} IS NULL), {col} {direction}, card_id ASC"


# --------------------------------------------------------------------------- #
# Индекс: сборка и исполнение
# --------------------------------------------------------------------------- #

class UsvoIndex:
    """Производный SQLite-индекс карточек. Пересобирается при изменении набора."""

    def __init__(
        self,
        records_provider: Callable[[], list[UsvoRecord]],
        last_updated_provider: Callable[[], float] | None = None,
        db_path: str = "./data/usvo_index.db",
    ):
        self.records_provider = records_provider
        self.last_updated_provider = last_updated_provider or (lambda: 0.0)
        self.db_path = db_path
        directory = os.path.dirname(db_path)
        if directory:
            os.makedirs(directory, exist_ok=True)
        self._lock = threading.Lock()
        self._signature: tuple | None = None
        with self._conn() as c:
            c.executescript(_SCHEMA)

    @contextmanager
    def _conn(self):
        conn = sqlite3.connect(self.db_path, timeout=10)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL;")
        # Встроенный SQLite lower() сворачивает только ASCII — кириллица «Видное» осталась
        # бы с заглавной. Регистрируем Python-casefold, чтобы текстовый поиск был
        # регистронезависим (используется в SQL как pyfold(...), см. _compile_cond).
        conn.create_function("pyfold", 1, _pyfold, deterministic=True)
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    # --- сборка -----------------------------------------------------------

    def _current_signature(self, records: list[UsvoRecord]) -> tuple:
        try:
            last = float(self.last_updated_provider() or 0)
        except Exception:  # noqa: BLE001
            last = 0.0
        return (len(records), last)

    def rebuild(self, records: list[UsvoRecord] | None = None) -> int:
        records = records if records is not None else self.records_provider()
        rows = [_row_for_record(r) for r in records]
        attrs: list[tuple] = []
        for r in records:
            attrs.extend(_attr_rows(r))
        placeholders = ", ".join("?" for _ in _COLUMNS)
        with self._conn() as conn:
            conn.execute("BEGIN")
            conn.execute("DELETE FROM usvo_index")
            conn.execute("DELETE FROM usvo_attr")
            conn.executemany(
                f"INSERT INTO usvo_index ({', '.join(_COLUMNS)}) VALUES ({placeholders})",
                rows,
            )
            conn.executemany(
                "INSERT OR IGNORE INTO usvo_attr (card_id, key, label, value, num, bool) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                attrs,
            )
            conn.commit()
        self._signature = self._current_signature(records)
        return len(rows)

    def ensure_current(self) -> None:
        with self._lock:
            records = self.records_provider()
            sig = self._current_signature(records)
            if sig != self._signature:
                self.rebuild(records)

    # --- исполнение -------------------------------------------------------

    def execute(self, clean: dict) -> dict:
        """Исполняет валидированный фильтр. Возвращает card_ids/total/aggregate."""
        self.ensure_current()
        params: list = []
        where_sql = _compile_group(clean.get("where"), params) or "1=1"
        base = f"FROM usvo_index WHERE {where_sql}"

        with self._conn() as conn:
            total = int(conn.execute(f"SELECT COUNT(*) AS n {base}", params).fetchone()["n"])

            aggregate = None
            agg_by = clean.get("aggregate_by")
            if clean.get("intent") == "aggregate" and agg_by:
                expr = _AGG_EXPR[agg_by]
                agg_rows = conn.execute(
                    f"SELECT {expr} AS k, COUNT(*) AS n {base} GROUP BY k ORDER BY n DESC, k ASC",
                    params,
                ).fetchall()
                aggregate = {str(r["k"]): int(r["n"]) for r in agg_rows}

            limit = clean.get("limit") or DEFAULT_LIMIT
            order = _order_clause(clean)
            id_rows = conn.execute(
                f"SELECT card_id {base}{order} LIMIT ?", [*params, limit]
            ).fetchall()
            card_ids = [int(r["card_id"]) for r in id_rows]

        return {
            "intent": clean.get("intent"),
            "card_ids": card_ids,
            "total": total,
            "aggregate": aggregate,
            "aggregate_by": agg_by or "",
        }

    def run(self, raw_filter: dict) -> dict:
        """Валидирует и исполняет сырой JSON-фильтр. Возвращает результат + замечания."""
        clean, errors = validate_filter(raw_filter)
        result = self.execute(clean)
        result["filter"] = clean
        result["warnings"] = errors
        return result


def field_catalog() -> dict:
    """Справочник полей/операторов — для документации и ответа API."""
    return {
        "fields": {name: spec["type"] for name, spec in FIELDS.items()},
        "operators_by_type": {
            "text": sorted(_TEXT_CMP), "enum": sorted(_TEXT_CMP),
            "int": sorted(_INT_CMP), "bool": sorted(_BOOL_CMP),
            "attr": sorted(_ATTR_CMP),
        },
        "aggregate_by": sorted(AGG_FIELDS),
        "order_by": sorted(ORDER_FIELDS),
        "intents": sorted(INTENTS),
    }
