"""Формирование знаний и мета-информации по карточкам УСВО для LLM/Dify."""
from __future__ import annotations

import datetime as dt
import hashlib
import re
from collections import Counter
from collections.abc import Callable, Iterable

from app.common.timez import human_ts, msk_today
from app.max.dify_client import SINGLE_CHUNK_PROCESS_RULE, DifyClient
from app.max.store import Store
from app.web.usvo import UsvoRecord

CARD_DOC_PREFIX = "Карточка УСВО #"
META_DOC_PREFIX = "Мета-информация УСВО"
SYNC_HASH_KEY = "usvo_knowledge_hash"


def _record_fields(record: UsvoRecord) -> list[tuple[str, str]]:
    seen: set[tuple[str, str]] = set()
    out: list[tuple[str, str]] = []
    for field in (*record.primary, *record.secondary, *record.extra):
        label = (field.label or "").strip()
        value = (field.value or "").strip()
        key = (label.casefold(), value.casefold())
        if not label or not value or key in seen:
            continue
        seen.add(key)
        out.append((label, value))
    return out


def card_document_prefix(card_id: int) -> str:
    return f"{CARD_DOC_PREFIX}{card_id}:"


def card_document_name(record: UsvoRecord) -> str:
    return f"{card_document_prefix(record.id)} {record.name}".strip()


def build_card_chunk(record: UsvoRecord) -> str:
    """Одна карточка → один самодостаточный текстовый чанк."""
    header = [
        f"ФИО: {record.name}",
        f"ID карточки: {record.id}",
        f"Статус: {record.status or 'нет данных'}",
        f"Дата рождения: {record.birth_date or 'нет данных'}",
        f"Телефон: {record.phone or 'нет данных'}",
        f"Адрес: {record.address or 'нет данных'}",
        f"Награды: {record.awards or 'нет данных'}",
        f"Источник карточки: {'загруженная/отредактированная' if record.source == 'uploaded' else 'основная таблица'}",
        f"Ссылка на карточку: /usvo/cards/{record.id}",
    ]
    sections = ["\n".join(header)]

    fields = _record_fields(record)
    if fields:
        sections.append("Все поля карточки:\n" + "\n".join(f"{label}: {value}" for label, value in fields))
    if record.head_directive and record.head_directive.get("text"):
        sections.append("Поручение Главы округа:\n" + str(record.head_directive["text"]).strip())
    if record.history_raw:
        sections.append("История взаимодействия:\n" + record.history_raw.strip())
    elif record.history:
        history_lines = []
        for event in record.history:
            if not isinstance(event, dict):
                continue
            parts = [
                str(event.get("date") or "").strip(),
                str(event.get("title") or "").strip(),
                str(event.get("detail") or "").strip(),
                str(event.get("status") or "").strip(),
            ]
            line = " — ".join(p for p in parts if p)
            if line:
                history_lines.append(line)
        if history_lines:
            sections.append("История взаимодействия:\n" + "\n".join(history_lines))
    return "\n\n".join(sections)


def _parse_date(value: str) -> dt.date | None:
    for fmt in ("%d.%m.%Y", "%Y-%m-%d", "%d.%m.%y"):
        try:
            return dt.datetime.strptime((value or "").strip(), fmt).date()
        except ValueError:
            continue
    return None


def _age(value: str) -> int | None:
    born = _parse_date(value)
    if born is None:
        return None
    today = msk_today()
    age = today.year - born.year - ((today.month, today.day) < (born.month, born.day))
    return age if 0 <= age < 120 else None


def _yes(value: str) -> bool | None:
    text = (value or "").strip().casefold()
    if not text or text in {"—", "-", "нет сведений", "не указано"}:
        return None
    if text.startswith("нет") or text in {"no", "не имеет", "отсутствует"}:
        return False
    return True


def _field_value(record: UsvoRecord, needles: tuple[str, ...]) -> str:
    for label, value in _record_fields(record):
        ll = label.casefold()
        if any(needle in ll for needle in needles):
            return value
    return ""


def _locality(address: str) -> str:
    text = (address or "").strip()
    match = re.search(r"(?:г\.|пос\.|п\.|с\.|дер\.|д\.)\s*([^,]+)", text, re.IGNORECASE)
    if match:
        return match.group(1).strip()
    parts = [part.strip() for part in text.split(",") if part.strip()]
    return parts[1] if len(parts) > 1 else (parts[0] if parts else "")


def _format_counter(counter: Counter, empty: str = "нет данных") -> str:
    if not counter:
        return empty
    return "; ".join(f"{key}: {value}" for key, value in counter.most_common())


class UsvoCardsMetaService:
    """Считает актуальные агрегаты напрямую по объединённому набору карточек."""

    def __init__(
        self,
        records_provider: Callable[[], list[UsvoRecord]],
        last_updated_provider: Callable[[], float] | None = None,
    ):
        self.records_provider = records_provider
        self.last_updated_provider = last_updated_provider or (lambda: 0.0)

    def build(self, records: Iterable[UsvoRecord] | None = None) -> dict:
        items = list(records if records is not None else self.records_provider())
        statuses = Counter((r.status or "не указан").strip() or "не указан" for r in items)
        sources = Counter("загруженные" if r.source == "uploaded" else "основная таблица" for r in items)
        support = Counter(
            (r.flags or {}).get("support_need", "").strip()
            for r in items
            if (r.flags or {}).get("support_need", "").strip()
        )
        localities = Counter(loc for r in items if (loc := _locality(r.address)))

        disability = Counter()
        gender = Counter()
        ages = Counter()
        labels = Counter()
        for record in items:
            disability_value = _field_value(record, ("инвалид", "группа инвалидности"))
            disability_flag = _yes(disability_value)
            disability["с инвалидностью" if disability_flag is True else
                       "без инвалидности" if disability_flag is False else "не указано"] += 1

            gender_value = _field_value(record, ("пол участника", "пол "))
            normalized_gender = gender_value.casefold()
            if normalized_gender.startswith("м"):
                gender["мужчины"] += 1
            elif normalized_gender.startswith("ж"):
                gender["женщины"] += 1
            else:
                gender["не указано"] += 1

            age = _age(record.birth_date)
            if age is None:
                ages["не указан"] += 1
            elif age < 25:
                ages["до 25"] += 1
            elif age < 35:
                ages["25–34"] += 1
            elif age < 45:
                ages["35–44"] += 1
            elif age < 55:
                ages["45–54"] += 1
            else:
                ages["55 и старше"] += 1
            labels.update(label for label, _ in _record_fields(record))

        flags = {
            "ветераны боевых действий": sum(bool((r.flags or {}).get("vbd")) for r in items),
            "нуждаются в трудоустройстве": sum(
                bool((r.flags or {}).get("unemployed")) for r in items
            ),
            "давно без связи": sum(bool((r.flags or {}).get("stale_contact")) for r in items),
            "участвуют в организациях": sum(
                bool((r.flags or {}).get("org_vremya"))
                or bool((r.flags or {}).get("org_geroi_mo"))
                or bool((r.flags or {}).get("org_assoc"))
                for r in items
            ),
            "имеют награды": sum(bool((r.awards or "").strip()) for r in items),
            "имеют поручение Главы": sum(bool(r.head_directive) for r in items),
        }
        last_updated = float(self.last_updated_provider() or 0)
        return {
            "total": len(items),
            "statuses": dict(statuses),
            "sources": dict(sources),
            "disability": dict(disability),
            "gender": dict(gender),
            "ages": dict(ages),
            "localities": dict(localities),
            "support_needs": dict(support),
            "flags": flags,
            "field_labels": [name for name, _ in labels.most_common(60)],
            "last_updated": last_updated,
        }

    def text(self, records: Iterable[UsvoRecord] | None = None) -> str:
        data = self.build(records)
        updated = (
            human_ts(data["last_updated"])
            if data["last_updated"] else "не определена"
        )
        flags = Counter(data["flags"])
        labels = "; ".join(data["field_labels"]) or "поля отсутствуют"
        return (
            "Мета-информация по базе карточек УСВО\n\n"
            f"Общее количество карточек: {data['total']}\n"
            f"Дата последнего обновления источников: {updated}\n"
            f"По статусам: {_format_counter(Counter(data['statuses']))}\n"
            f"По инвалидности: {_format_counter(Counter(data['disability']))}\n"
            f"По полу: {_format_counter(Counter(data['gender']))}\n"
            f"По возрастным группам: {_format_counter(Counter(data['ages']))}\n"
            f"По населённым пунктам/районам: {_format_counter(Counter(data['localities']))}\n"
            f"По типам требуемой помощи: {_format_counter(Counter(data['support_needs']))}\n"
            f"Дополнительные показатели: {_format_counter(flags)}\n"
            f"Источники: {_format_counter(Counter(data['sources']))}\n\n"
            "Структура данных: каждая карточка содержит шапку с ID, ФИО, статусом, "
            "датой рождения, телефоном, адресом и наградами, а также произвольные "
            "первичные, вторичные и дополнительные поля, поручения и историю взаимодействия.\n"
            f"Доступные названия полей: {labels}"
        )


class UsvoCardKnowledgeSyncService:
    """Best-effort синхронизация карточек и мета-документа с Dify Dataset API."""

    def __init__(
        self,
        store: Store,
        dify: DifyClient,
        records_provider: Callable[[], list[UsvoRecord]],
        meta: UsvoCardsMetaService,
        *,
        auto_sync: bool = True,
    ):
        self.store = store
        self.dify = dify
        self.records_provider = records_provider
        self.meta = meta
        self.auto_sync = auto_sync

    def ready(self) -> bool:
        return self.dify.kb_ready()

    @staticmethod
    def _ok(result: dict) -> bool:
        status = int(result.get("_status") or 200)
        return not result.get("skipped") and status < 400

    def signature(self, records: Iterable[UsvoRecord] | None = None) -> str:
        items = list(records if records is not None else self.records_provider())
        digest = hashlib.sha256()
        for record in sorted(items, key=lambda item: item.id):
            digest.update(build_card_chunk(record).encode("utf-8"))
            digest.update(b"\0")
        digest.update(self.meta.text(items).encode("utf-8"))
        return digest.hexdigest()

    def mark_current(self, records: Iterable[UsvoRecord] | None = None) -> None:
        self.store.set_ai_sync_state(SYNC_HASH_KEY, self.signature(records))

    async def sync_card(self, record: UsvoRecord) -> dict:
        if not self.ready():
            return {"ok": False, "error": "База знаний УСВО в Dify не настроена."}
        result = await self.dify.upsert_document_text(
            card_document_name(record),
            build_card_chunk(record),
            match_prefix=card_document_prefix(record.id),
            process_rule=SINGLE_CHUNK_PROCESS_RULE,
        )
        if not self._ok(result):
            return {"ok": False, "error": "Dify не подтвердил обновление карточки."}
        return {"ok": True, "id": record.id}

    async def remove_card(self, card_id: int) -> dict:
        if not self.ready():
            return {"ok": False, "error": "База знаний УСВО в Dify не настроена."}
        removed = await self.dify.delete_documents_by_prefix(card_document_prefix(card_id))
        return {"ok": True, "removed": removed}

    async def sync_meta(self, records: Iterable[UsvoRecord] | None = None) -> dict:
        if not self.ready():
            return {"ok": False, "error": "База знаний УСВО в Dify не настроена."}
        result = await self.dify.upsert_document_text(
            META_DOC_PREFIX,
            self.meta.text(records),
            match_prefix=META_DOC_PREFIX,
        )
        return {"ok": self._ok(result)}

    async def rebuild(self) -> dict:
        if not self.ready():
            return {"ok": False, "error": "База знаний УСВО в Dify не настроена."}
        records = self.records_provider()
        errors: list[str] = []
        try:
            await self.dify.delete_documents_by_prefix(CARD_DOC_PREFIX)
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "error": f"Не удалось очистить старые документы: {exc}"}
        for record in records:
            try:
                result = await self.dify.add_document_text(
                    card_document_name(record),
                    build_card_chunk(record),
                    process_rule=SINGLE_CHUNK_PROCESS_RULE,
                )
                if not self._ok(result):
                    errors.append(str(record.id))
            except Exception:
                errors.append(str(record.id))
        try:
            meta_result = await self.sync_meta(records)
        except Exception:
            meta_result = {"ok": False}
        if not meta_result.get("ok"):
            errors.append("meta")
        if errors:
            return {
                "ok": False,
                "count": len(records) - len([e for e in errors if e != "meta"]),
                "errors": errors,
                "error": "Часть документов не синхронизирована.",
            }
        self.mark_current(records)
        return {"ok": True, "count": len(records)}

    async def ensure_current(self) -> dict:
        if not self.auto_sync:
            return {"ok": True, "skipped": True, "reason": "auto_sync=false"}
        if not self.ready():
            return {"ok": False, "skipped": True, "error": "База знаний УСВО не настроена."}
        try:
            current = self.signature()
            if self.store.get_ai_sync_state(SYNC_HASH_KEY) == current:
                return {"ok": True, "changed": False}
            result = await self.rebuild()
            result["changed"] = True
            return result
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "changed": False, "error": f"Ошибка синхронизации: {exc}"}
