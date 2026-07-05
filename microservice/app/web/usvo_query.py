"""Детерминированный движок запросов по всем карточкам УСВО.

Закрывает ограничение модели в 32k токенов. У qwen3-32b нельзя держать в контексте
1500 карточек, а RAG (knowledge-retrieval) возвращает лишь top-k релевантных чанков —
значит исчерпывающие вопросы («перечисли всех с орденом Мужества», «сколько в посёлке X
нуждаются в работе») он закрыть не может. Микросервис при этом держит ВСЕ карточки
(`records_provider()`), поэтому такие вопросы мы считаем точно в Python и отдаём модели
компактный полный результат, а семантический поиск оставляем за RAG.

Поток: `plan_query` (вопрос → структурированный QuerySpec; Dify-планировщик со
structured_output, офлайн-фолбэк по ключевым словам) → `execute_query` (точная выборка по
всем записям) → `format_result` (компактный текст с бюджетом по объёму).
"""
from __future__ import annotations

import json
import logging
import re
from collections import Counter
from dataclasses import dataclass, field

from app.web.usvo import UsvoRecord
from app.web.usvo_index import UsvoIndex, validate_filter
from app.web.usvo_knowledge import (
    UsvoCardsMetaService,
    _age,
    _field_value,
    _locality,
    _record_fields,
    _yes,
)

log = logging.getLogger("web.usvo_query")

# Бюджет символов на детерминированный блок (≈ половина в токенах для кириллицы).
# Держим заметно ниже 32k, оставляя место под систему/историю/RAG-контекст/ответ.
DEFAULT_MAX_CHARS = 24000

INTENTS = {"list", "count", "aggregate", "lookup", "semantic"}
AGGREGATE_FIELDS = {"status", "locality", "gender", "age", "disability", "awards"}


# --- Предикаты флагов (зеркало WebService._filtered_records, чтобы выборки совпадали) ---

def _org_covered(r: UsvoRecord) -> bool:
    f = r.flags or {}
    return bool(f.get("org_vremya") or f.get("org_geroi_mo") or f.get("org_assoc"))


def _has_awards(r: UsvoRecord) -> bool:
    a = (r.awards or "").strip().lower()
    return bool(a) and "нет" not in a and a not in ("—", "-")


FLAG_PREDICATES = {
    "vbd": lambda r: bool((r.flags or {}).get("vbd")),
    "unemployed": lambda r: bool((r.flags or {}).get("unemployed")),
    "stale_contact": lambda r: bool((r.flags or {}).get("stale_contact")),
    "support_need": lambda r: bool((r.flags or {}).get("support_need")),
    "org": _org_covered,
    "awards": _has_awards,
    "directive": lambda r: bool(r.head_directive),
}

# Слова-маркеры для офлайн-распознавания флагов в вопросе.
_FLAG_KEYWORDS = {
    "unemployed": ("трудоустрой", "трудоустро", "работ", "безработ", "не трудоустро"),
    "vbd": ("ветеран боевых", "вбд", "ветеранов боевых"),
    "stale_contact": ("без связи", "давно не выход", "давно не звон", "потеряна связь"),
    # support_need оставляем узким, чтобы «нуждается в работе» не путать с мерами поддержки.
    "support_need": ("мер поддержк", "меры поддержк", "социальной поддержк"),
    "org": ("организац", "время героя", "герои подмосковья", "ассоциац"),
    "awards": ("наград", "награжд"),
    "directive": ("поручени глав", "глава округа", "главы округа", "встреч с глав"),
}

# Маркеры, что вопрос вообще про награды (чтобы «боевые действия» не считать медалью).
_AWARD_TRIGGERS = ("орден", "медал", "наград", "награжд", "отваг", "благодарн", "отличи")


@dataclass
class QuerySpec:
    """Структурированное представление вопроса для точной выборки."""

    intent: str = "semantic"
    status: str = ""
    locality: str = ""
    awards_contains: str = ""
    gender: str = ""           # "м" | "ж" | ""
    disability: str = ""       # "yes" | "no" | ""
    age_min: int | None = None
    age_max: int | None = None
    flags: list[str] = field(default_factory=list)
    field_label: str = ""      # подстрока названия произвольного поля
    field_value: str = ""      # искомая подстрока его значения
    name_query: str = ""
    sort: str = ""             # "age_desc" | "age_asc" | "name"
    limit: int | None = None
    aggregate_by: str = ""

    def normalized(self) -> "QuerySpec":
        if self.intent not in INTENTS:
            self.intent = "semantic"
        if self.gender:
            self.gender = self.gender.strip().lower()[:1]
        if self.disability:
            self.disability = "yes" if str(self.disability).strip().lower() in (
                "yes", "да", "true", "1", "с инвалидностью"
            ) else "no"
        self.flags = [f for f in (self.flags or []) if f in FLAG_PREDICATES]
        if self.aggregate_by and self.aggregate_by not in AGGREGATE_FIELDS:
            self.aggregate_by = ""
        if self.intent == "aggregate" and not self.aggregate_by:
            self.aggregate_by = "status"
        return self

    @property
    def has_filters(self) -> bool:
        return bool(
            self.status or self.locality or self.awards_contains or self.gender
            or self.disability or self.age_min is not None or self.age_max is not None
            or self.flags or self.field_label or self.field_value or self.name_query
        )


@dataclass
class QueryResult:
    intent: str
    matched: list[UsvoRecord]
    total: int
    aggregate: dict | None = None
    aggregate_by: str = ""


# --------------------------------------------------------------------------- #
# Выполнение запроса (детерминированно, по всем карточкам)
# --------------------------------------------------------------------------- #

def _matches(spec: QuerySpec, r: UsvoRecord) -> bool:
    if spec.status and spec.status.casefold() not in (r.status or "").casefold():
        return False
    if spec.name_query and spec.name_query.casefold() not in (r.name or "").casefold():
        return False
    if spec.locality:
        loc = _locality(r.address).casefold()
        addr = (r.address or "").casefold()
        if spec.locality.casefold() not in loc and spec.locality.casefold() not in addr:
            return False
    if spec.awards_contains and spec.awards_contains.casefold() not in (r.awards or "").casefold():
        return False
    if spec.gender:
        g = _field_value(r, ("пол участника", "пол ")).casefold()
        if not g.startswith(spec.gender):
            return False
    if spec.disability:
        flag = _yes(_field_value(r, ("инвалид", "группа инвалидности")))
        if (flag is True) != (spec.disability == "yes"):
            return False
    if spec.age_min is not None or spec.age_max is not None:
        age = _age(r.birth_date)
        if age is None:
            return False
        if spec.age_min is not None and age < spec.age_min:
            return False
        if spec.age_max is not None and age > spec.age_max:
            return False
    for flag in spec.flags:
        pred = FLAG_PREDICATES.get(flag)
        if pred and not pred(r):
            return False
    if spec.field_label or spec.field_value:
        ok = False
        for label, value in _record_fields(r):
            if (not spec.field_label or spec.field_label.casefold() in label.casefold()) and (
                not spec.field_value or spec.field_value.casefold() in value.casefold()
            ):
                ok = True
                break
        if not ok:
            return False
    return True


def _age_group(age: int | None) -> str:
    if age is None:
        return "не указан"
    if age < 25:
        return "до 25"
    if age < 35:
        return "25–34"
    if age < 45:
        return "35–44"
    if age < 55:
        return "45–54"
    return "55 и старше"


def _aggregate(items: list[UsvoRecord], by: str) -> dict:
    counter: Counter = Counter()
    for r in items:
        if by == "status":
            counter[(r.status or "не указан").strip() or "не указан"] += 1
        elif by == "locality":
            counter[_locality(r.address) or "не указан"] += 1
        elif by == "gender":
            g = _field_value(r, ("пол участника", "пол ")).casefold()
            counter["мужчины" if g.startswith("м") else "женщины" if g.startswith("ж") else "не указано"] += 1
        elif by == "age":
            counter[_age_group(_age(r.birth_date))] += 1
        elif by == "disability":
            flag = _yes(_field_value(r, ("инвалид", "группа инвалидности")))
            counter["с инвалидностью" if flag is True else "без инвалидности" if flag is False else "не указано"] += 1
        elif by == "awards":
            counter["с наградами" if _has_awards(r) else "без наград"] += 1
    return dict(counter.most_common())


def execute_query(spec: QuerySpec, records: list[UsvoRecord]) -> QueryResult:
    spec = spec.normalized()
    matched = [r for r in records if _matches(spec, r)] if spec.has_filters else list(records)
    if spec.sort == "age_desc":
        matched.sort(key=lambda r: (_age(r.birth_date) is None, -(_age(r.birth_date) or 0)))
    elif spec.sort == "age_asc":
        matched.sort(key=lambda r: (_age(r.birth_date) is None, _age(r.birth_date) or 0))
    elif spec.sort == "name":
        matched.sort(key=lambda r: (r.name or "").casefold())
    total = len(matched)
    aggregate = _aggregate(matched, spec.aggregate_by) if spec.intent == "aggregate" else None
    limited = matched[: spec.limit] if spec.limit else matched
    return QueryResult(
        intent=spec.intent,
        matched=limited,
        total=total,
        aggregate=aggregate,
        aggregate_by=spec.aggregate_by,
    )


# --------------------------------------------------------------------------- #
# Форматирование результата для контекста модели
# --------------------------------------------------------------------------- #

_AGG_LABELS = {
    "status": "по статусам",
    "locality": "по населённым пунктам",
    "gender": "по полу",
    "age": "по возрастным группам",
    "disability": "по инвалидности",
    "awards": "по наличию наград",
}


def _describe(spec: QuerySpec) -> str:
    parts: list[str] = []
    if spec.status:
        parts.append(f"статус содержит «{spec.status}»")
    if spec.locality:
        parts.append(f"населённый пункт «{spec.locality}»")
    if spec.awards_contains:
        parts.append(f"награда «{spec.awards_contains}»")
    if spec.gender:
        parts.append("мужчины" if spec.gender == "м" else "женщины")
    if spec.disability:
        parts.append("с инвалидностью" if spec.disability == "yes" else "без инвалидности")
    if spec.age_min is not None:
        parts.append(f"возраст от {spec.age_min}")
    if spec.age_max is not None:
        parts.append(f"возраст до {spec.age_max}")
    for flag in spec.flags:
        parts.append(_FLAG_DESCR.get(flag, flag))
    if spec.field_label or spec.field_value:
        parts.append(f"поле «{spec.field_label or '*'}» ~ «{spec.field_value or '*'}»")
    if spec.name_query:
        parts.append(f"ФИО содержит «{spec.name_query}»")
    if not parts:
        return "Критерий: все карточки базы."
    return "Критерии выборки: " + "; ".join(parts) + "."


_FLAG_DESCR = {
    "vbd": "ветераны боевых действий",
    "unemployed": "нуждаются в трудоустройстве",
    "stale_contact": "давно без связи",
    "support_need": "указана потребность в мерах поддержки",
    "org": "участвуют в организациях",
    "awards": "имеют награды",
    "directive": "есть поручение Главы округа",
}


def _record_line(idx: int, r: UsvoRecord) -> str:
    extras: list[str] = []
    status = (r.status or "").strip()
    if status and status != "—":
        extras.append(status)
    loc = _locality(r.address)
    if loc:
        extras.append(loc)
    if _has_awards(r):
        extras.append(f"награды: {r.awards.strip()}")
    tail = ("; " + "; ".join(extras)) if extras else ""
    return f"{idx}. {r.name} (карточка #{r.id}){tail}"


def format_result(result: QueryResult, spec: QuerySpec, max_chars: int = DEFAULT_MAX_CHARS) -> str:
    lines = [_describe(spec), f"Всего подходящих карточек: {result.total}."]
    if result.aggregate:
        lines.append(f"Разбивка {_AGG_LABELS.get(result.aggregate_by, '')}:")
        lines.extend(f"- {key}: {val}" for key, val in result.aggregate.items())

    if result.intent in ("list", "lookup"):
        lines.append("")
        lines.append("Карточки (ФИО пиши полностью — они станут ссылками на карточку):")
        shown = 0
        for i, r in enumerate(result.matched, 1):
            entry = _record_line(i, r)
            if len("\n".join(lines)) + len(entry) + 1 > max_chars:
                break
            lines.append(entry)
            shown += 1
        if shown < result.total:
            lines.append(
                f"… показано {shown} из {result.total} карточек "
                "(список усечён по объёму; сообщи пользователю точное общее число и "
                "предложи уточнить фильтры для полного перечня)."
            )
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# Планирование запроса: Dify-планировщик + офлайн-фолбэк
# --------------------------------------------------------------------------- #

_COUNT_WORDS = ("сколько", "количество", "число", "скольк")
_LIST_WORDS = ("перечисли", "перечислите", "список", "спиок", "выведи всех", "покажи всех",
               "кто все", "назови всех", "укажи всех", "всех, кто", "всех кто")
_AGG_WORDS = ("распредел", "разбив", "статистик", "по статус", "по возраст", "по полу",
              "по населённ", "по населен", "сводк")
# Вопросительные/перечислительные маркеры для апгрейда semantic → list при фильтрах.
_WHO_CUES = ("кто", "у кого", "какие", "каких", "назови", "выведи", "покажи", "список", "перечисл")


def _detect_intent(q: str) -> str:
    if any(w in q for w in _AGG_WORDS):
        return "aggregate"
    if any(w in q for w in _COUNT_WORDS):
        return "count"
    if any(w in q for w in _LIST_WORDS):
        return "list"
    return "semantic"


def _detect_aggregate_by(q: str) -> str:
    if "возраст" in q:
        return "age"
    if "пол" in q:
        return "gender"
    if "инвалид" in q:
        return "disability"
    if "населённ" in q or "населен" in q or "посёлк" in q or "поселк" in q or "район" in q:
        return "locality"
    if "наград" in q:
        return "awards"
    return "status"


def _status_in_question(status: str, q: str) -> bool:
    """True, если статус упомянут в вопросе — с учётом склонений (по основе слова)."""
    s = status.casefold().strip()
    if not s:
        return False
    if s in q:
        return True
    word = s.split()[0]
    stem = word[: max(5, len(word) - 3)]
    return len(stem) >= 5 and stem in q


def plan_offline(question: str, meta: UsvoCardsMetaService | None = None) -> QuerySpec:
    """Эвристический разбор вопроса без LLM. Узнаёт частые фильтры/агрегаты."""
    q = " ".join((question or "").split()).casefold()
    spec = QuerySpec(intent=_detect_intent(q))

    # Статусы — по известным значениям из меты (надёжнее общих слов). Сопоставляем по
    # основе слова, чтобы ловить склонения («добровольцев» → «доброволец»).
    if meta is not None:
        try:
            for status in (meta.build().get("statuses") or {}):
                if status and status.strip() and _status_in_question(status, q):
                    spec.status = status.strip()
                    break
        except Exception:  # noqa: BLE001
            pass

    # Награды: распознаём конкретную награду только если вопрос «наградный».
    if any(t in q for t in _AWARD_TRIGGERS):
        if "мужеств" in q:
            spec.awards_contains = "мужеств"
        elif "отваг" in q:
            spec.awards_contains = "отваг"
        elif "благодарн" in q:
            spec.awards_contains = "благодарн"
        elif "боев" in q or "отличи" in q:
            spec.awards_contains = "боев"
        elif "медал" in q:
            spec.awards_contains = "медал"
        else:
            spec.flags.append("awards")  # просто «с наградами», без уточнения

    # Флаги по ключевым словам (конкретная награда уже учтена подстрокой).
    for flag, words in _FLAG_KEYWORDS.items():
        if flag == "awards" and spec.awards_contains:
            continue
        if any(w in q for w in words):
            spec.flags.append(flag)
    spec.flags = list(dict.fromkeys(spec.flags))

    # Пол / инвалидность.
    if "мужчин" in q:
        spec.gender = "м"
    elif "женщин" in q:
        spec.gender = "ж"
    if "инвалид" in q and "disability" not in q:
        spec.disability = "yes"

    # Возраст: «старше N», «младше/моложе N», «от A до B», «N лет».
    m = re.search(r"старше\s+(\d{1,3})", q)
    if m:
        spec.age_min = int(m.group(1))
    m = re.search(r"(?:младше|моложе|до)\s+(\d{1,3})\s*(?:лет|год)", q)
    if m:
        spec.age_max = int(m.group(1))
    m = re.search(r"от\s+(\d{1,3})\s+до\s+(\d{1,3})", q)
    if m:
        spec.age_min, spec.age_max = int(m.group(1)), int(m.group(2))

    # Населённый пункт: «в посёлке/селе/городе/деревне X».
    m = re.search(r"(?:в|из|по)\s+(?:посёлк\w*|поселк\w*|селе|село|городе|город|деревне|деревня|д\.|пос\.|п\.|с\.)\s+([а-яё\-]+)", q)
    if m:
        spec.locality = m.group(1).strip()

    if spec.intent == "aggregate":
        spec.aggregate_by = _detect_aggregate_by(q)

    # «Самые старшие/младшие N».
    m = re.search(r"(?:топ|первы\w+)\s+(\d{1,3})", q)
    if m:
        spec.limit = int(m.group(1))
    # «Кто/у кого/какие …» + распознанные фильтры → это запрос-перечисление.
    # Информационные вопросы («расскажи про меры поддержки») остаются semantic.
    if spec.intent == "semantic" and spec.has_filters and any(c in q for c in _WHO_CUES):
        spec.intent = "list"

    if "старш" in q and spec.intent in ("list", "lookup"):
        spec.sort = "age_desc"
    elif ("младш" in q or "молож" in q) and spec.intent in ("list", "lookup"):
        spec.sort = "age_asc"

    return spec.normalized()


_PLANNER_PROMPT = """Ты — планировщик запросов по базе карточек УСВО. Преврати вопрос
пользователя в JSON-объект QuerySpec (без пояснений, только JSON). Поля:
intent: "list" | "count" | "aggregate" | "lookup" | "semantic"
  - count: спрашивают число/сколько; list: перечислить/список; aggregate: разбивка/статистика;
    lookup: о конкретном человеке по ФИО; semantic: свободный смысловой вопрос (тогда фильтры пустые).
status, locality, awards_contains, name_query: строки-подстроки (или "").
gender: "м" | "ж" | ""; disability: "yes" | "no" | "".
age_min, age_max: целые или null.
flags: подмножество ["vbd","unemployed","stale_contact","support_need","org","awards","directive"].
field_label, field_value: подстроки произвольного поля карточки (или "").
sort: "age_desc" | "age_asc" | "name" | ""; limit: целое или null.
aggregate_by: "status"|"locality"|"gender"|"age"|"disability"|"awards" (для intent=aggregate).
Если вопрос не про выборку/счёт по базе, ставь intent="semantic" и оставляй фильтры пустыми.

Вопрос пользователя:
"""


def _spec_from_dict(data: dict) -> QuerySpec:
    spec = QuerySpec()
    for key in (
        "intent", "status", "locality", "awards_contains", "gender", "disability",
        "field_label", "field_value", "name_query", "sort", "aggregate_by",
    ):
        val = data.get(key)
        if isinstance(val, str):
            setattr(spec, key, val.strip())
    for key in ("age_min", "age_max", "limit"):
        val = data.get(key)
        # 0/отрицательное от планировщика трактуем как «не задано» (limit=0 иначе
        # обнулил бы выборку, age_min=0 — бессмысленный фильтр).
        if isinstance(val, (int, float)) and int(val) > 0:
            setattr(spec, key, int(val))
    flags = data.get("flags")
    if isinstance(flags, list):
        spec.flags = [str(f) for f in flags]
    return spec.normalized()


def _extract_json(text: str) -> dict | None:
    text = (text or "").strip()
    if not text:
        return None
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(0))
        except json.JSONDecodeError:
            return None
    return None


async def plan_query(
    question: str,
    planner=None,
    meta: UsvoCardsMetaService | None = None,
    *,
    user_key: str = "web-ai-planner",
) -> QuerySpec:
    """Возвращает QuerySpec: через Dify-планировщик (если настроен), иначе офлайн."""
    if planner is not None and getattr(planner, "app_ready", lambda: False)():
        try:
            result = await planner.ask_text(_PLANNER_PROMPT + question, user_key=user_key)
            data = _extract_json(result.get("answer", ""))
            if data is not None:
                return _spec_from_dict(data)
            log.warning("Планировщик Dify вернул не-JSON — офлайн-фолбэк")
        except Exception as exc:  # noqa: BLE001
            log.warning("Планировщик Dify недоступен (%s) — офлайн-фолбэк", exc)
    return plan_offline(question, meta)


async def legacy_build_query_context(
    question: str,
    records: list[UsvoRecord],
    meta: UsvoCardsMetaService | None = None,
    planner=None,
    max_chars: int = DEFAULT_MAX_CHARS,
) -> str:
    """Старый путь: плоский QuerySpec + Python-скан. Фолбэк, если индекс не подан."""
    spec = await plan_query(question, planner, meta)
    if spec.intent == "semantic":
        return ""
    result = execute_query(spec, records)
    return format_result(result, spec, max_chars)


# =========================================================================== #
# Новый pipeline: вопрос → JSON-фильтр (DSL) → валидация → SQLite-индекс → текст
#
# Расширяет старый QuerySpec: вложенные AND/OR, числовые сравнения и доступ к
# любому полю карточки (attr:<key>). Это закрывает составные и числовые вопросы
# («инвалидность 2 группы И >3 детей», «больше трёх детей»), которые плоская схема
# выразить не могла. Фильтрация и подсчёты идут в SQLite (app/web/usvo_index.py).
# =========================================================================== #

# Инструкция-планировщик (дублирует системный промпт dify/usvo_filter_assistant.yml;
# прикладывается к вопросу при вызове Dify-планировщика и служит документацией DSL).
_FILTER_PROMPT = """Ты — планировщик запросов по базе карточек УСВО (участники СВО).
Преврати вопрос пользователя в строгий JSON-фильтр по схеме ниже. Только JSON, без markdown.

Схема:
{
  "intent": "count" | "list" | "aggregate" | "lookup" | "semantic",
  "where": { "match": "all" | "any",
             "conditions": [ {"field": ..., "cmp": ..., "value": ...} | {"match": ..., "conditions": [...]} ] },
  "aggregate_by": "" | <поле агрегации>,
  "order_by": "" | "age" | "name" | "children_count" | "awards_count" | "disability_group" | "status" | "locality",
  "order_dir": "asc" | "desc",
  "limit": целое или 0
}

intent: count — «сколько/число»; list — «перечисли/покажи всех/кто»; aggregate — «распредели/
статистика/разбивка»; lookup — о конкретном человеке по ФИО; semantic — вопрос НЕ про выборку по
базе (общая справка, «что положено», совет) → тогда where=null и фильтры пустые.

Поля (field) и их типы/операторы:
- Текстовые (cmp: contains|=|!=|in|exists): name, status, locality, region, address, marital,
  support_need, awards, education, profession, phone, source. Для нечёткого совпадения — contains.
- gender (enum, cmp: =): значения "м" | "ж".
- Числовые (cmp: =|!=|>|>=|<|<=|between|in|exists): age, children_count, minor_children,
  disabled_children, disability_group, awards_count.
- Булевы (cmp: = со значением true/false, либо exists): disability, vbd, unemployed,
  needs_support, has_awards, in_org, org_vremya, org_geroi_mo, org_assoc, head_directive, stale_contact.
- Произвольное поле карточки: field="attr:<название поля>" (cmp: contains|=|>|>=|<|<=|between|exists).
  Используй ТОЛЬКО если подходящего канонического поля выше нет.

aggregate_by (для intent=aggregate): status, locality, gender, age_bucket, disability_group,
children_count, disability, has_awards, in_org, vbd, marital, education, source, needs_support, unemployed.

Семантика:
- «дети/ребёнок» → children_count; «несовершеннолетние/малолетние дети» → minor_children;
  «дети-инвалиды / с ограниченными возможностями» → disabled_children; «многодетные» → children_count>=3.
- «инвалидность N группы» → disability_group = N; просто «инвалид» → disability = true.
- «ветеран боевых действий / ВБД» → vbd; «нужна работа / трудоустройство» → unemployed;
  «давно без связи» → stale_contact; «в организациях / Время героя / Герои Подмосковья / ассоциация» → in_org.
- «орден Мужества / медаль …» → awards contains "...", или has_awards для «есть награды».
- Возраст: «старше N» → age> N; «младше/моложе N» → age< N; «от A до B» → age between [A,B].
- Несколько условий через «и» → match="all"; через «или» → match="any".

Примеры:
Вопрос: «сколько человек имеют больше трёх детей»
{"intent":"count","where":{"match":"all","conditions":[{"field":"children_count","cmp":">","value":3}]}}
Вопрос: «найди всех с инвалидностью 2 группы и двумя детьми»
{"intent":"list","where":{"match":"all","conditions":[{"field":"disability_group","cmp":"=","value":2},{"field":"children_count","cmp":"=","value":2}]}}
Вопрос: «сколько мобилизованных в Видном с орденом Мужества»
{"intent":"count","where":{"match":"all","conditions":[{"field":"status","cmp":"contains","value":"мобилизован"},{"field":"locality","cmp":"contains","value":"Видное"},{"field":"awards","cmp":"contains","value":"Мужества"}]}}
Вопрос: «распредели участников по возрастным группам»
{"intent":"aggregate","aggregate_by":"age_bucket"}
Вопрос: «у кого высшее образование и нужна работа»
{"intent":"list","where":{"match":"all","conditions":[{"field":"education","cmp":"contains","value":"высшее"},{"field":"unemployed","cmp":"=","value":true}]}}
Вопрос: «что положено многодетной семье участника СВО»
{"intent":"semantic","where":null}

Вопрос пользователя:
"""

# Старый aggregate_by (плоский QuerySpec) → новые поля агрегации индекса.
_AGG_BY_MAP = {
    "status": "status", "locality": "locality", "gender": "gender",
    "age": "age_bucket", "disability": "disability", "awards": "has_awards",
}

_NUMWORD = {
    "один": 1, "одного": 1, "одна": 1, "одним": 1, "одной": 1,
    "два": 2, "две": 2, "двое": 2, "двух": 2, "двумя": 2, "двоих": 2,
    "три": 3, "трое": 3, "трёх": 3, "трех": 3, "тремя": 3, "троих": 3,
    "четыре": 4, "четверо": 4, "четырёх": 4, "четырех": 4, "четырьмя": 4, "четверых": 4,
    "пять": 5, "пятеро": 5, "пятью": 5, "пятерых": 5, "шесть": 6, "шестью": 6,
}
_ORD_GROUP = {"перв": 1, "втор": 2, "трет": 3}
_ROMAN_GROUP = {"iii": 3, "ii": 2, "i": 1}


def _numword(token: str) -> int | None:
    token = token.strip().casefold()
    if token.isdigit():
        return int(token)
    return _NUMWORD.get(token)


def _spec_to_conditions(spec: QuerySpec) -> list[dict]:
    """Переводит распознанный офлайн QuerySpec в список условий нового DSL."""
    conds: list[dict] = []
    if spec.status:
        conds.append({"field": "status", "cmp": "contains", "value": spec.status})
    if spec.locality:
        conds.append({"field": "locality", "cmp": "contains", "value": spec.locality})
    if spec.awards_contains:
        conds.append({"field": "awards", "cmp": "contains", "value": spec.awards_contains})
    if spec.gender:
        conds.append({"field": "gender", "cmp": "=", "value": spec.gender})
    if spec.disability == "yes":
        conds.append({"field": "disability", "cmp": "=", "value": True})
    elif spec.disability == "no":
        conds.append({"field": "disability", "cmp": "=", "value": False})
    if spec.age_min is not None:
        conds.append({"field": "age", "cmp": ">=", "value": spec.age_min})
    if spec.age_max is not None:
        conds.append({"field": "age", "cmp": "<=", "value": spec.age_max})
    if spec.name_query:
        conds.append({"field": "name", "cmp": "contains", "value": spec.name_query})
    _flag_field = {
        "vbd": "vbd", "unemployed": "unemployed", "stale_contact": "stale_contact",
        "support_need": "needs_support", "org": "in_org", "awards": "has_awards",
        "directive": "head_directive",
    }
    for flag in spec.flags:
        field = _flag_field.get(flag)
        if field:
            conds.append({"field": field, "cmp": "=", "value": True})
    if spec.field_label or spec.field_value:
        cond = {"field": f"attr:{spec.field_label or spec.field_value}"}
        if spec.field_value:
            cond["cmp"] = "contains"
            cond["value"] = spec.field_value
        else:
            cond["cmp"] = "exists"
            cond["value"] = None
        conds.append(cond)
    return conds


def _children_conditions(q: str) -> list[dict]:
    """Числовые условия по детям из вопроса (то, что плоский QuerySpec не ловил)."""
    out: list[dict] = []
    kids = r"(?:дет\w*|реб[ёе]н\w*|ребёнк\w*|ребенк\w*|ребят\w*)"
    m = re.search(rf"(?:больше|более|свыше)\s+(\w+)\s+{kids}", q)
    if m and (n := _numword(m.group(1))) is not None:
        out.append({"field": "children_count", "cmp": ">", "value": n})
        return out
    m = re.search(rf"(?:меньше|менее)\s+(\w+)\s+{kids}", q)
    if m and (n := _numword(m.group(1))) is not None:
        out.append({"field": "children_count", "cmp": "<", "value": n})
        return out
    m = re.search(rf"(?:не менее|от)\s+(\w+)\s+{kids}", q) or \
        re.search(rf"(\w+)\s+(?:и более|или более|и больше)\s+{kids}", q)
    if m and (n := _numword(m.group(1))) is not None:
        out.append({"field": "children_count", "cmp": ">=", "value": n})
        return out
    if "многодетн" in q:
        out.append({"field": "children_count", "cmp": ">=", "value": 3})
        return out
    if re.search(r"(?:без детей|нет детей|бездетн)", q):
        out.append({"field": "children_count", "cmp": "=", "value": 0})
        return out
    m = re.search(rf"(\w+)\s+{kids}", q)
    if m and (n := _numword(m.group(1))) is not None:
        out.append({"field": "children_count", "cmp": "=", "value": n})
    elif re.search(r"(?:есть дети|с детьми|имеют детей|имеет детей|с ребёнк|с ребенк)", q):
        out.append({"field": "children_count", "cmp": ">=", "value": 1})
    if re.search(r"(?:несовершеннолетн|малолетн)", q):
        out.append({"field": "minor_children", "cmp": ">=", "value": 1})
    return out


def _education_condition(q: str) -> dict | None:
    """«с высшим образованием» / «среднее профессиональное» → education contains …."""
    if "образован" not in q and "высшим" not in q and "высшее" not in q:
        return None
    if "высш" in q:
        return {"field": "education", "cmp": "contains", "value": "высшее"}
    if "профессиональн" in q or "спо " in q or " спо" in q:
        return {"field": "education", "cmp": "contains", "value": "профессиональн"}
    if "среднее общее" in q or "общее образован" in q:
        return {"field": "education", "cmp": "contains", "value": "общее"}
    if "среднее" in q:
        return {"field": "education", "cmp": "contains", "value": "среднее"}
    return None


def _disability_group_condition(q: str) -> dict | None:
    """«инвалидность 2 группы» / «второй группы» → disability_group = N."""
    if "групп" not in q:
        return None
    m = re.search(r"(\d)\s*-?\s*(?:й|я|ая|ой|ую|е|ей)?\s*групп", q)
    if m:
        g = int(m.group(1))
        return {"field": "disability_group", "cmp": "=", "value": g} if 1 <= g <= 3 else None
    for stem, val in _ORD_GROUP.items():
        if re.search(rf"{stem}\w*\s+групп", q):
            return {"field": "disability_group", "cmp": "=", "value": val}
    m = re.search(r"\b(iii|ii|i)\s*групп", q)
    if m:
        return {"field": "disability_group", "cmp": "=", "value": _ROMAN_GROUP[m.group(1)]}
    return None


def plan_offline_v2(question: str, meta: UsvoCardsMetaService | None = None) -> dict:
    """Эвристический разбор вопроса в новый DSL-фильтр (без LLM)."""
    spec = plan_offline(question, meta)
    q = " ".join((question or "").split()).casefold()
    conds = _spec_to_conditions(spec)

    child_conds = _children_conditions(q)
    # Если поймали число детей, убираем грубый attr-фолбэк по тем же словам.
    if child_conds:
        conds = [c for c in conds if not str(c.get("field", "")).startswith("attr:")]
        conds.extend(child_conds)
    group_cond = _disability_group_condition(q)
    if group_cond:
        conds = [c for c in conds if c.get("field") != "disability_group"]
        conds.append(group_cond)
    edu_cond = _education_condition(q)
    if edu_cond and not any(c.get("field") == "education" for c in conds):
        conds.append(edu_cond)

    intent = spec.intent
    if conds and intent == "semantic":
        intent = "count" if any(w in q for w in _COUNT_WORDS) else "list"

    filt: dict = {
        "intent": intent,
        "where": {"match": "all", "conditions": conds} if conds else None,
        "limit": spec.limit or 0,
    }
    if intent == "aggregate":
        filt["aggregate_by"] = _AGG_BY_MAP.get(spec.aggregate_by, spec.aggregate_by or "status")
    if spec.sort == "age_desc":
        filt["order_by"], filt["order_dir"] = "age", "desc"
    elif spec.sort == "age_asc":
        filt["order_by"], filt["order_dir"] = "age", "asc"
    elif spec.sort == "name":
        filt["order_by"], filt["order_dir"] = "name", "asc"
    return filt


async def plan_filter(
    question: str,
    planner=None,
    meta: UsvoCardsMetaService | None = None,
    *,
    user_key: str = "web-ai-filter",
) -> dict:
    """Сырой JSON-фильтр: через Dify-планировщик (если настроен), иначе офлайн-эвристика."""
    if planner is not None and getattr(planner, "app_ready", lambda: False)():
        try:
            result = await planner.ask_text(_FILTER_PROMPT + question, user_key=user_key)
            data = _extract_json(result.get("answer", ""))
            if isinstance(data, dict):
                return data
            log.warning("Фильтр-планировщик Dify вернул не-JSON — офлайн-фолбэк")
        except Exception as exc:  # noqa: BLE001
            log.warning("Фильтр-планировщик Dify недоступен (%s) — офлайн-фолбэк", exc)
    return plan_offline_v2(question, meta)


# --- форматирование результата SQLite-запроса для контекста модели ---------- #

_AGG_LABELS_V2 = {
    "status": "по статусам", "locality": "по населённым пунктам", "gender": "по полу",
    "age_bucket": "по возрастным группам", "disability_group": "по группам инвалидности",
    "children_count": "по числу детей", "disability": "по инвалидности",
    "has_awards": "по наличию наград", "in_org": "по участию в организациях",
    "vbd": "по статусу ветерана БД", "marital": "по семейному положению",
    "education": "по образованию", "source": "по источнику карточки",
    "needs_support": "по потребности в поддержке", "unemployed": "по потребности в работе",
}

_FIELD_RU = {
    "name": "ФИО", "status": "статус", "gender": "пол", "age": "возраст",
    "birth_date": "дата рождения", "locality": "населённый пункт", "region": "регион",
    "address": "адрес", "marital": "семейное положение", "children_count": "число детей",
    "minor_children": "несовершеннолетних детей", "disabled_children": "детей-инвалидов",
    "disability": "инвалидность", "disability_group": "группа инвалидности",
    "vbd": "ветеран боевых действий", "unemployed": "нуждается в трудоустройстве",
    "needs_support": "нужны меры поддержки", "support_need": "потребность в поддержке",
    "awards_count": "число наград", "has_awards": "наличие наград", "awards": "награды",
    "in_org": "участие в организациях", "head_directive": "поручение Главы",
    "stale_contact": "давно без связи", "education": "образование",
    "profession": "профессия", "phone": "телефон", "source": "источник",
}

_CMP_RU = {">": ">", ">=": "≥", "<": "<", "<=": "≤", "=": "=", "!=": "≠",
           "contains": "содержит", "in": "из", "between": "в диапазоне", "exists": "указано"}


def _describe_condition(cond: dict) -> str:
    field = cond.get("field", "")
    name = _FIELD_RU.get(field, field if not field.startswith("attr:") else f"поле «{field[5:]}»")
    cmp = _CMP_RU.get(cond.get("cmp"), cond.get("cmp"))
    value = cond.get("value")
    if cond.get("cmp") == "exists":
        return f"{name}: указано"
    if isinstance(value, list):
        value = ", ".join(str(v) for v in value)
    elif isinstance(value, bool):
        value = "да" if value else "нет"
    return f"{name} {cmp} {value}"


def _describe_group(group: dict | None) -> str:
    if not group or not group.get("conditions"):
        return "все карточки базы"
    joiner = " и " if group.get("match") == "all" else " или "
    parts = []
    for cond in group["conditions"]:
        if "match" in cond and "conditions" in cond:
            parts.append("(" + _describe_group(cond) + ")")
        else:
            parts.append(_describe_condition(cond))
    return joiner.join(parts)


def format_db_result(
    question: str,
    clean: dict,
    result: dict,
    records: list[UsvoRecord],
    max_chars: int = DEFAULT_MAX_CHARS,
) -> str:
    """Компактный авторитетный блок «Точные данные по всей базе» для контекста модели."""
    by_id = {r.id: r for r in records}
    lines = [
        f"Критерии выборки: {_describe_group(clean.get('where'))}.",
        f"Всего подходящих карточек: {result['total']}.",
    ]
    if result.get("aggregate"):
        label = _AGG_LABELS_V2.get(result.get("aggregate_by"), "")
        lines.append(f"Разбивка {label}:")
        lines.extend(f"- {key}: {val}" for key, val in result["aggregate"].items())

    if clean.get("intent") in ("list", "lookup"):
        matched = [by_id[i] for i in result["card_ids"] if i in by_id]
        lines.append("")
        lines.append("Карточки (ФИО пиши полностью — они станут ссылками на карточку):")
        shown = 0
        for i, rec in enumerate(matched, 1):
            entry = _record_line(i, rec)
            if len("\n".join(lines)) + len(entry) + 1 > max_chars:
                break
            lines.append(entry)
            shown += 1
        if shown < result["total"]:
            lines.append(
                f"… показано {shown} из {result['total']} карточек "
                "(список усечён по объёму; сообщи пользователю точное общее число и "
                "предложи уточнить фильтры для полного перечня)."
            )
    return "\n".join(lines)


async def build_query_context(
    question: str,
    records: list[UsvoRecord],
    meta: UsvoCardsMetaService | None = None,
    planner=None,
    index: UsvoIndex | None = None,
    max_chars: int = DEFAULT_MAX_CHARS,
) -> str:
    """Готовый блок «Точные данные по всей базе» (или "" для semantic-вопроса).

    Новый путь (есть индекс): вопрос → JSON-фильтр → валидация → SQLite. Без индекса —
    старый QuerySpec + Python-скан (legacy-фолбэк).
    """
    if index is None:
        return await legacy_build_query_context(question, records, meta, planner, max_chars)
    raw = await plan_filter(question, planner, meta)
    clean, _errors = validate_filter(raw)
    if clean.get("intent") == "semantic":
        return ""
    result = index.execute(clean)
    return format_db_result(question, clean, result, records, max_chars)
