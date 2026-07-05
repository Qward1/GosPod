"""Сервисный слой веб-кабинета: обращения, карточки УСВО, аналитика, ИИ.

Связывает источники данных (таблица УСВО, SQLite-состояние бота, Dify) в формат,
удобный фронтенду. Реальные обращения берутся из таблицы `escalations` (их создаёт
MAX-бот). Если их ещё нет и включён `web.seed_appeals` — показываются обращения,
синтезированные из реальных карточек УСВО. Ответы на такие обращения хранятся в
памяти процесса.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import random
import re
import sqlite3
import time

from app.config import Config
from app.docs.forms import application_summary, build_application_docx, normalize_application
from app.docs import measures as measures_mod
from app.docs.templates import extract_placeholders, fill_template_docx
from app.max.client import MaxClient
from app.max.dify_client import DifyClient
from app.max.store import Store
from app.web import ai, export
from app.web.analytics import build_analytics
from app.web.history import build_history
from app.web.history_ai import normalize_history
from app.web.import_usvo import build_template_xlsx, parse_usvo_xlsx
from app.web.insight import analyze_sentiment, summarize
from app.web.topics import classify_topic
from app.web.usvo import (
    UsvoRecord,
    UsvoStore,
    card_identity,
    find_field_value,
    record_identity,
)
from app.web.usvo_db import USVO_DB_ID_BASE, UsvoCardStore, is_db_id

APP_STATUS_LABELS = {
    "proposed": "Предложено гражданину",
    "awaiting_confirm": "Ожидает подтверждения",
    "submitted": "Подано — на рассмотрении",
    "approved": "Одобрено",
    "rejected": "Отклонено",
}

SEED_QUESTIONS = [
    "Как оформить статус ветерана боевых действий?",
    "Какие выплаты положены после ранения?",
    "Положена ли компенсация за санаторно-курортное лечение?",
    "Как получить помощь в трудоустройстве?",
    "Можно ли пройти бесплатное переобучение?",
    "Какие льготы есть на оплату ЖКХ?",
    "Как записаться на психологическую консультацию?",
    "Положена ли матери погибшего пенсия по потере кормильца?",
    "Как получить технические средства реабилитации?",
    "Какие меры поддержки есть для детей участника СВО?",
    "Нужна юридическая консультация по жилищному вопросу.",
    "Как вступить в ассоциацию ветеранов СВО?",
]


def _digits(s: str) -> str:
    return re.sub(r"\D", "", s or "")


class WebService:
    def __init__(
        self,
        cfg: Config,
        store: Store,
        usvo_store: UsvoStore,
        dify: DifyClient,
        max_client: MaxClient,
        employees=None,
    ):
        self.cfg = cfg
        self.store = store
        self.usvo = usvo_store
        # Карточки УСВО, загруженные оператором из веб-кабинета (хранятся в БД).
        self.usvo_db = UsvoCardStore(store, cfg.web.contact_stale_days)
        self.dify = dify
        self.max_client = max_client
        # Учётные записи сотрудников (отдельная БД). Имена активных сотрудников —
        # это список ответственных, которых выбирает администратор по обращению.
        self.employees = employees
        self._seed_appeals: list[dict] | None = None
        self._seed_appointments = 0
        self.usvo_knowledge = None

    # ---- объединённый источник карточек (загруженные из БД + табличные) -----

    def _all_records(self) -> list[UsvoRecord]:
        """Все карточки: сначала загруженные из кабинета, затем из Excel-таблицы.

        Защита от дубликатов: если табличная карточка совпадает по идентичности
        (ФИО + дата рождения / телефон) с уже загруженной/отредактированной, она
        скрывается — показывается только версия из базы (в ней правки оператора).
        """
        db_cards = self.usvo_db.all()
        seen = {record_identity(r) for r in db_cards}
        seen.discard("")
        table = [r for r in self.usvo.all() if record_identity(r) not in seen]
        return db_cards + table

    def _get_record(self, rec_id: int) -> UsvoRecord | None:
        if is_db_id(rec_id):
            return self.usvo_db.get(rec_id)
        return self.usvo.get(rec_id)

    def all_usvo_records(self) -> list[UsvoRecord]:
        """Публичный источник карточек для чата, мета-агрегатов и KB-синхронизации."""
        return self._all_records()

    def usvo_last_updated(self) -> float:
        """Последнее изменение Excel-источника или загруженных SQLite-карточек."""
        timestamps: list[float] = []
        try:
            if os.path.exists(self.usvo.path):
                timestamps.append(float(os.path.getmtime(self.usvo.path)))
        except OSError:
            pass
        for row in self.store.list_usvo_cards():
            data = dict(row)
            timestamps.append(float(data.get("updated_at") or data.get("created_at") or 0))
        return max(timestamps, default=0.0)

    def attach_usvo_knowledge(self, service) -> None:
        self.usvo_knowledge = service

    async def _sync_usvo_knowledge(
        self,
        *,
        card_ids: list[int] | None = None,
        removed_ids: list[int] | None = None,
        rebuild: bool = False,
    ) -> dict:
        """Best-effort: ошибки Dify не должны откатывать сохранение карточки."""
        service = self.usvo_knowledge
        if service is None:
            return {"ok": False, "skipped": True, "error": "Сервис знаний не подключён."}
        if not service.ready():
            return {"ok": False, "skipped": True, "error": "База знаний УСВО не настроена."}
        try:
            if rebuild:
                return await service.rebuild()
            for card_id in removed_ids or []:
                await service.remove_card(card_id)
            for card_id in card_ids or []:
                record = self._get_record(card_id)
                if record is not None:
                    await service.sync_card(record)
            await service.sync_meta()
            service.mark_current()
            return {"ok": True}
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "error": f"Ошибка синхронизации карточек: {exc}"}

    def _operators(self) -> list[str]:
        """Список ответственных = активные сотрудники из отдельной БД.

        Если администратор ещё не создал ни одного сотрудника — откатываемся к
        статическому списку из конфига, чтобы кабинет оставался рабочим.
        """
        if self.employees is not None:
            names = [n.strip() for n in self.employees.active_names() if n.strip()]
            if names:
                return names
        return [op.strip() for op in self.cfg.web.operators if op.strip()] or ["Оператор администрации"]

    # ---- совместимость со старыми Store на сервере ------------------------

    def _store_path(self) -> str:
        return getattr(self.store, "path", self.cfg.storage.sqlite_path)

    def _ensure_column(self, conn, table: str, column: str, ddl: str) -> None:
        cols = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}
        if column not in cols:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}")

    def _count_escalations(self) -> int:
        if hasattr(self.store, "count_escalations"):
            return int(self.store.count_escalations())
        with sqlite3.connect(self._store_path(), timeout=10) as conn:
            conn.row_factory = sqlite3.Row
            return int(conn.execute("SELECT COUNT(*) AS n FROM escalations").fetchone()["n"])

    def _list_escalations(self) -> list:
        if hasattr(self.store, "list_escalations"):
            return self.store.list_escalations()
        with sqlite3.connect(self._store_path(), timeout=10) as conn:
            conn.row_factory = sqlite3.Row
            return conn.execute("SELECT * FROM escalations ORDER BY id DESC LIMIT 500").fetchall()

    def _get_escalation(self, escalation_id: int):
        if hasattr(self.store, "get_escalation"):
            return self.store.get_escalation(escalation_id)
        with sqlite3.connect(self._store_path(), timeout=10) as conn:
            conn.row_factory = sqlite3.Row
            return conn.execute("SELECT * FROM escalations WHERE id = ?", (escalation_id,)).fetchone()

    def _set_escalation_assignee(self, escalation_id: int, assignee: str) -> None:
        if hasattr(self.store, "set_escalation_assignee"):
            self.store.set_escalation_assignee(escalation_id, assignee)
            return
        with sqlite3.connect(self._store_path(), timeout=10) as conn:
            conn.row_factory = sqlite3.Row
            self._ensure_column(conn, "escalations", "assignee", "TEXT")
            conn.execute("UPDATE escalations SET assignee = ? WHERE id = ?", (assignee, escalation_id))

    def _set_escalation_answer(self, escalation_id: int, answer: str, assignee: str = "") -> None:
        if hasattr(self.store, "set_escalation_answer"):
            self.store.set_escalation_answer(escalation_id, answer, assignee)
            return
        with sqlite3.connect(self._store_path(), timeout=10) as conn:
            conn.row_factory = sqlite3.Row
            self._ensure_column(conn, "escalations", "answer", "TEXT")
            self._ensure_column(conn, "escalations", "answered_at", "REAL")
            self._ensure_column(conn, "escalations", "assignee", "TEXT")
            if assignee:
                conn.execute(
                    "UPDATE escalations SET answer = ?, answered_at = ?, status = 'answered', "
                    "assignee = ? WHERE id = ?",
                    (answer, time.time(), assignee, escalation_id),
                )
            else:
                conn.execute(
                    "UPDATE escalations SET answer = ?, answered_at = ?, status = 'answered' WHERE id = ?",
                    (answer, time.time(), escalation_id),
                )

    def _delete_escalation(self, escalation_id: int) -> bool:
        if hasattr(self.store, "delete_escalation"):
            return bool(self.store.delete_escalation(escalation_id))
        with sqlite3.connect(self._store_path(), timeout=10) as conn:
            conn.row_factory = sqlite3.Row
            conn.execute("DELETE FROM operator_state WHERE escalation_id = ?", (escalation_id,))
            cur = conn.execute("DELETE FROM escalations WHERE id = ?", (escalation_id,))
            return cur.rowcount > 0

    def _list_appointments(self) -> list:
        if hasattr(self.store, "list_appointments"):
            return self.store.list_appointments()
        with sqlite3.connect(self._store_path(), timeout=10) as conn:
            conn.row_factory = sqlite3.Row
            return conn.execute("SELECT * FROM appointments ORDER BY id DESC LIMIT 1000").fetchall()

    # ---- сопоставление гражданина с карточкой УСВО -------------------------

    def _match_usvo(self, name: str = "", phone: str = "") -> UsvoRecord | None:
        ph = _digits(phone)
        recs = self._all_records()
        if ph:
            for r in recs:
                rec_phone = _digits(r.phone)
                if rec_phone and (ph in rec_phone or rec_phone in ph):
                    return r
        nm = (name or "").strip().lower()
        if nm:
            for r in recs:
                if nm and (nm in r.name.lower() or r.name.lower() in nm):
                    return r
        return None

    # ---- синтезированные обращения ----------------------------------------

    def _build_seed_appeals(self) -> list[dict]:
        recs = self._all_records()
        operators = self._operators()
        rng = random.Random(42)  # детерминированно — стабильный набор
        sample = recs[:12] if len(recs) >= 12 else recs
        out: list[dict] = []
        now = time.time()
        for i, rec in enumerate(sample):
            q = SEED_QUESTIONS[i % len(SEED_QUESTIONS)]
            created = now - rng.randint(0, 18) * 86400 - rng.randint(0, 86400)
            answered = i % 3 == 0  # каждое третье — отвечено
            out.append({
                "id": f"seed-{i+1}",
                "real": False,
                "question": q,
                "created_at": created,
                "topic": classify_topic(q),
                "assignee": operators[i % len(operators)],
                "status": "answered" if answered else "open",
                "answer": "Ответ направлен гражданину." if answered else "",
                "citizen": {"name": rec.name, "phone": rec.phone, "username": "",
                            "user_id": f"seed-usvo-{rec.id}"},
                "usvo_id": rec.id,
            })
        self._seed_appointments = sum(1 for a in out if a["status"] == "answered")
        out.sort(key=lambda a: a["created_at"], reverse=True)
        return out

    def _seeded_appeals(self) -> list[dict]:
        if self._seed_appeals is None:
            self._seed_appeals = self._build_seed_appeals()
        return self._seed_appeals

    def _use_seeded_appeals(self) -> bool:
        return self.cfg.web.seed_appeals and self._count_escalations() == 0

    # ---- обращения ---------------------------------------------------------

    def _escalation_to_appeal(self, row) -> dict:
        d = dict(row)
        question = d.get("question") or ""
        topic = d.get("topic") or classify_topic(question)
        usvo = self._match_usvo(d.get("user_name") or "", "")
        return {
            "id": f"esc-{d['id']}",
            "real": True,
            "question": question,
            "created_at": d.get("created_at") or 0,
            "topic": topic,
            "assignee": d.get("assignee") or "",
            "status": d.get("status") or "open",
            "answer": d.get("answer") or "",
            "citizen": {
                "name": d.get("user_name") or "",
                "phone": "",
                "username": d.get("username") or "",
                "user_id": d.get("user_id") or "",
            },
            "usvo_id": usvo.id if usvo else None,
        }

    def list_appeals(self) -> list[dict]:
        if self._use_seeded_appeals():
            appeals = list(self._seeded_appeals())
        else:
            appeals = [self._escalation_to_appeal(r) for r in self._list_escalations()]
        for a in appeals:
            a["created_human"] = _human_ts(a["created_at"])
            # Лёгкие ИИ-инсайты для таблицы: тональность (смайл-индикатор) и
            # «суть кратко» в одну строку. Детерминированно, без обращения к LLM.
            a["sentiment"] = analyze_sentiment(a.get("question", ""))
            a["summary"] = summarize(a.get("question", ""))
        return appeals

    def get_appeal(self, appeal_id: str) -> dict | None:
        for a in self.list_appeals():
            if a["id"] == appeal_id:
                return a
        return None

    def set_assignee(self, appeal_id: str, assignee: str) -> dict | None:
        assignee = (assignee or "").strip()
        if appeal_id.startswith("esc-"):
            self._set_escalation_assignee(int(appeal_id[4:]), assignee)
        else:
            for a in self._seeded_appeals():
                if a["id"] == appeal_id:
                    a["assignee"] = assignee
        return self.get_appeal(appeal_id)

    async def draft(self, appeal_id: str, operator: str) -> dict:
        appeal = self.get_appeal(appeal_id)
        if not appeal:
            return {"error": "not_found"}
        usvo = self._get_record(appeal["usvo_id"]) if appeal.get("usvo_id") else None
        if usvo is None:
            usvo = self._match_usvo(appeal["citizen"]["name"], appeal["citizen"]["phone"])
        # Имя в шапке ответа — РЕАЛЬНОЕ имя гражданина из MAX (а не из случайно
        # сопоставленной карточки УСВО). Карточка по-прежнему даёт контекст для тела.
        citizen_name = (appeal.get("citizen") or {}).get("name") or ""
        return await ai.draft_reply(
            self.cfg, self.dify,
            question=appeal["question"],
            usvo=usvo,
            operator=(operator or "").strip() or self._operators()[0],
            citizen_name=citizen_name,
        )

    def appeal_history(self, appeal_id: str) -> dict:
        """История обращений того же гражданина (по его id в MAX).

        Возвращает профиль гражданина из БД пользователей MAX + список его прошлых
        обращений с ответами. Для демо-обращений группируется по синтетическому id.
        """
        appeal = self.get_appeal(appeal_id)
        if not appeal:
            return {"error": "not_found"}
        user_id = (appeal.get("citizen") or {}).get("user_id") or ""
        profile = None
        items: list[dict] = []

        if appeal.get("real") and user_id:
            urow = self.store.get_user(user_id)
            if urow:
                u = dict(urow)
                profile = {
                    "user_id": user_id,
                    "name": u.get("name") or "",
                    "username": u.get("username") or "",
                    "phone": u.get("phone") or "",
                    "question_count": u.get("question_count") or 0,
                }
            for row in self.store.list_escalations_by_user(user_id):
                d = dict(row)
                items.append({
                    "id": f"esc-{d['id']}",
                    "question": d.get("question") or "",
                    "answer": d.get("answer") or "",
                    "status": d.get("status") or "open",
                    "created_human": _human_ts(d.get("created_at") or 0),
                    "is_current": f"esc-{d['id']}" == appeal_id,
                })
        else:
            # Демо-режим: все обращения с тем же синтетическим user_id.
            for a in self.list_appeals():
                if (a.get("citizen") or {}).get("user_id") == user_id:
                    items.append({
                        "id": a["id"], "question": a.get("question") or "",
                        "answer": a.get("answer") or "", "status": a.get("status") or "open",
                        "created_human": a.get("created_human") or "—",
                        "is_current": a["id"] == appeal_id,
                    })
            if not profile:
                profile = {
                    "user_id": user_id,
                    "name": (appeal.get("citizen") or {}).get("name") or "",
                    "username": "", "phone": (appeal.get("citizen") or {}).get("phone") or "",
                    "question_count": len(items),
                }

        return {"profile": profile, "items": items, "count": len(items)}

    async def answer(self, appeal_id: str, answer_text: str, assignee: str) -> dict:
        appeal = self.get_appeal(appeal_id)
        if not appeal:
            return {"error": "not_found"}
        assignee = (assignee or "").strip()

        delivered = False
        kb_saved = False
        if appeal["real"] and appeal_id.startswith("esc-"):
            esc_id = int(appeal_id[4:])
            self._set_escalation_answer(esc_id, answer_text, assignee)
            # запись ответа в базу знаний (как делает бот) — best-effort
            try:
                res = await self.dify.add_to_kb(appeal["question"], answer_text)
                kb_saved = not res.get("skipped")
            except Exception:  # noqa: BLE001
                kb_saved = False
            # отправка ответа гражданину в MAX — best-effort
            row = self._get_escalation(esc_id)
            if row and row["user_id"] and (self.cfg.max.bot_token or "").strip() and \
                    "xxxx" not in self.cfg.max.bot_token.lower():
                try:
                    await self.max_client.send_message(answer_text, user_id=row["user_id"])
                    delivered = True
                except Exception:  # noqa: BLE001
                    delivered = False
        else:
            for a in self._seeded_appeals():
                if a["id"] == appeal_id:
                    a["status"] = "answered"
                    a["answer"] = answer_text
                    if assignee:
                        a["assignee"] = assignee

        return {"ok": True, "delivered_to_citizen": delivered, "saved_to_kb": kb_saved,
                "appeal": self.get_appeal(appeal_id)}

    def delete_appeal(self, appeal_id: str) -> dict:
        if appeal_id.startswith("esc-"):
            deleted = self._delete_escalation(int(appeal_id[4:]))
            return {"ok": deleted}
        before = len(self._seeded_appeals())
        self._seed_appeals = [a for a in self._seeded_appeals() if a["id"] != appeal_id]
        return {"ok": len(self._seed_appeals) < before}

    # ---- карточки УСВО -----------------------------------------------------

    def _filtered_records(self, query: str = "", filters: dict | None = None) -> list[UsvoRecord]:
        """Карточки, отфильтрованные по тексту и набору флагов (для списка и выгрузки)."""
        filters = filters or {}
        recs = self._all_records()
        q = (query or "").strip().lower()
        q_digits = _digits(q)

        def org_covered(r: UsvoRecord) -> bool:
            f = r.flags or {}
            return bool(f.get("org_vremya") or f.get("org_geroi_mo") or f.get("org_assoc"))

        def has_awards(r: UsvoRecord) -> bool:
            a = (r.awards or "").strip().lower()
            return bool(a) and "нет" not in a and a not in ("—", "-")

        out: list[UsvoRecord] = []
        for r in recs:
            haystack = " ".join(
                [r.name, r.short_name, r.phone or "", r.status or "", r.address or ""]
            ).lower()
            if q and q not in haystack and (not q_digits or q_digits not in _digits(r.phone)):
                continue
            flags = r.flags or {}
            st = (filters.get("status") or "").strip().lower()
            if st and st != (r.status or "").strip().lower():
                continue
            if _tri(filters.get("vbd"), flags.get("vbd")):
                continue
            if _tri(filters.get("employment"), flags.get("unemployed")):
                continue
            if _tri(filters.get("contact"), flags.get("stale_contact")):
                continue
            if _tri(filters.get("org"), org_covered(r)):
                continue
            if _tri(filters.get("awards"), has_awards(r)):
                continue
            if _tri(filters.get("directive"), bool(r.head_directive)):
                continue
            src = (filters.get("source") or "").strip().lower()
            if src in ("uploaded", "table") and r.source != src:
                continue
            out.append(r)
        return out

    def list_usvo(self, query: str = "", filters: dict | None = None) -> list[dict]:
        out = []
        for r in self._filtered_records(query, filters):
            out.append({
                "id": r.id, "name": r.name, "short_name": r.short_name,
                "initials": r.initials, "status": r.status, "phone": r.phone,
                "call_date": r.call_date, "address": r.address,
                "flags": r.flags,
                "head_directive": r.head_directive,
                "source": r.source,
            })
        return out

    def usvo_statuses(self) -> list[str]:
        """Уникальные статусы для выпадающего фильтра."""
        seen: list[str] = []
        for r in self._all_records():
            s = (r.status or "").strip()
            if s and s != "—" and s not in seen:
                seen.append(s)
        return sorted(seen)

    def get_usvo(self, rec_id: int) -> dict | None:
        r = self._get_record(rec_id)
        if not r:
            return None
        d = r.as_dict()
        appeals = [a for a in self.list_appeals() if a.get("usvo_id") == rec_id]
        d["appeals"] = appeals
        if r.source == "uploaded":
            # Загруженная карточка: нормализованная «История взаимодействия» из БД
            # + реальные обращения, отсортированные вместе.
            d["history"] = self._merge_uploaded_history(r, appeals)
            d["history_raw"] = r.history_raw
        else:
            # Табличная карточка: синтетические события + реальные обращения.
            d["history"] = build_history(r, appeals)
        return d

    def _merge_uploaded_history(self, r: UsvoRecord, appeals: list[dict]) -> list[dict]:
        events = list(r.history or [])
        for a in appeals:
            events.append({
                "date": a.get("created_human") or "",
                "kind": "appeal",
                "status": "выполнено" if a.get("status") == "answered" else "в работе",
                "style": "ok" if a.get("status") == "answered" else "accent",
                "title": "Обращение в контакт-центр",
                "detail": a.get("question") or "",
                "org": "Бот MAX · Единый центр поддержки",
            })
        return events

    async def suggestions(self, rec_id: int) -> dict:
        r = self._get_record(rec_id)
        if not r:
            return {"error": "not_found"}
        return await ai.suggest_measures(self.cfg, self.dify, usvo=r)

    # ---- загрузка / выгрузка карточек УСВО ---------------------------------

    async def import_usvo(self, file_bytes: bytes, replace: bool = False) -> dict:
        """Парсит загруженный Excel, нормализует историю взаимодействия и сохраняет."""
        try:
            cards = parse_usvo_xlsx(file_bytes)
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "error": f"Не удалось прочитать файл: {exc}"}
        if not cards:
            return {"ok": False, "error": "В файле не найдено ни одной карточки."}

        if replace:
            self.store.clear_usvo_cards()

        # Идентичности уже имеющихся карточек (загруженных + табличных). Совпадающие
        # карточки из файла пропускаем — это и есть защита от дубликатов: версия,
        # лежащая в базе (с правками оператора), остаётся, копия из Excel не плодится.
        existing = {record_identity(r) for r in self._all_records()}
        existing.discard("")

        import time as _t
        batch = f"import-{int(_t.time())}"
        saved = 0
        saved_ids: list[int] = []
        skipped = 0
        for card in cards:
            ident = card_identity(card.get("name", ""), card.get("birth_date", ""),
                                  card.get("phone", ""))
            if ident and ident in existing:
                skipped += 1
                continue
            events = await normalize_history(self.cfg, card.get("history_raw", ""),
                                             user_key=f"usvo-import-{batch}")
            data = json.dumps({
                "fields": card.get("fields", []),
                "history_raw": card.get("history_raw", ""),
                "history": events,
                "head_directive": None,
            }, ensure_ascii=False)
            row_id = self.store.add_usvo_card({
                "name": card.get("name", ""), "status": card.get("status", ""),
                "phone": card.get("phone", ""), "address": card.get("address", ""),
                "birth_date": card.get("birth_date", ""), "call_date": card.get("call_date", ""),
                "awards": card.get("awards", ""), "data": data,
            }, batch=batch)
            saved_ids.append(USVO_DB_ID_BASE + row_id)
            if ident:
                existing.add(ident)
            saved += 1
        knowledge_sync = await self._sync_usvo_knowledge(
            card_ids=saved_ids,
            rebuild=replace,
        )
        return {
            "ok": True,
            "saved": saved,
            "skipped": skipped,
            "replaced": replace,
            "total_uploaded": self.usvo_db.count(),
            "knowledge_sync": knowledge_sync,
        }

    def usvo_template(self) -> bytes:
        return build_template_xlsx()

    async def update_usvo(self, rec_id: int, fields: list[dict],
                          history_raw: str | None = None) -> dict:
        """Сохраняет отредактированную карточку УСВО.

        Карточка хранится как список полей (label→value); ключевые значения (ФИО,
        статус, телефон…) пересчитываются из них по смыслу заголовка. Табличная
        карточка (из Excel задания 1) при редактировании сохраняется как загруженная
        («оверрайд»): благодаря единой идентичности (ФИО + дата рождения / телефон)
        она перекрывает исходную табличную и в списке не двоится.
        """
        clean = [
            {"label": (f.get("label") or "").strip(), "value": (f.get("value") or "").strip()}
            for f in (fields or [])
            if (f.get("label") or "").strip() and (f.get("value") or "").strip()
        ]
        if not clean:
            return {"ok": False, "error": "Карточка не может быть пустой."}
        flist = [(f["label"], f["value"]) for f in clean]
        header = {
            "name": find_field_value(flist, "name"),
            "status": find_field_value(flist, "status"),
            "phone": find_field_value(flist, "phone"),
            "address": find_field_value(flist, "address"),
            "birth_date": find_field_value(flist, "birth"),
            "call_date": find_field_value(flist, "call_date"),
            "awards": find_field_value(flist, "awards"),
        }
        new_raw = (history_raw or "").strip() if history_raw is not None else None

        if is_db_id(rec_id):
            row = self.store.get_usvo_card(rec_id - USVO_DB_ID_BASE)
            if not row:
                return {"ok": False, "error": "not_found"}
            try:
                old = json.loads(dict(row).get("data") or "{}")
            except Exception:  # noqa: BLE001
                old = {}
            head_directive = old.get("head_directive")
            old_raw = (old.get("history_raw") or "").strip()
            if new_raw is None or new_raw == old_raw:
                # История не менялась — оставляем уже нормализованные события.
                history, stored_raw = old.get("history") or [], old.get("history_raw") or ""
            elif new_raw:
                history = await normalize_history(self.cfg, new_raw,
                                                  user_key=f"usvo-edit-{rec_id}")
                stored_raw = new_raw
            else:
                history, stored_raw = [], ""
            data = json.dumps({
                "fields": clean, "history_raw": stored_raw,
                "history": history, "head_directive": head_directive,
            }, ensure_ascii=False)
            ok = self.store.update_usvo_card(rec_id - USVO_DB_ID_BASE, {**header, "data": data})
            if not ok:
                return {"ok": False, "error": "not_found"}
            knowledge_sync = await self._sync_usvo_knowledge(card_ids=[rec_id])
            return {"ok": True, "id": rec_id, "knowledge_sync": knowledge_sync}

        # Табличная карточка → создаём загруженный оверрайд.
        orig = self.usvo.get(rec_id)
        if not orig:
            return {"ok": False, "error": "not_found"}
        head_directive = orig.head_directive
        if new_raw:
            history = await normalize_history(self.cfg, new_raw, user_key=f"usvo-edit-{rec_id}")
            stored_raw = new_raw
        else:
            # Текста истории нет — сохраняем синтетическую ленту, чтобы она не исчезла.
            history, stored_raw = build_history(orig, []), ""
        data = json.dumps({
            "fields": clean, "history_raw": stored_raw,
            "history": history, "head_directive": head_directive,
        }, ensure_ascii=False)
        new_row_id = self.store.add_usvo_card({**header, "data": data}, batch="edit")
        new_id = USVO_DB_ID_BASE + new_row_id
        knowledge_sync = await self._sync_usvo_knowledge(
            card_ids=[new_id], removed_ids=[rec_id]
        )
        return {"ok": True, "id": new_id, "knowledge_sync": knowledge_sync}

    async def delete_usvo(self, rec_id: int) -> dict:
        if not is_db_id(rec_id):
            return {"ok": False, "error": "Табличные карточки удалять нельзя — только загруженные."}
        ok = self.store.delete_usvo_card(rec_id - USVO_DB_ID_BASE)
        if not ok:
            return {"ok": False}
        # После удаления оверрайда может снова проявиться одноимённая карточка из
        # основной Excel-таблицы, поэтому безопаснее пересобрать весь набор.
        knowledge_sync = await self._sync_usvo_knowledge(rebuild=True)
        return {"ok": True, "knowledge_sync": knowledge_sync}

    async def clear_uploaded_usvo(self) -> dict:
        deleted = self.store.clear_usvo_cards()
        knowledge_sync = await self._sync_usvo_knowledge(rebuild=True)
        return {"ok": True, "deleted": deleted, "knowledge_sync": knowledge_sync}

    # ---- выгрузка (export) -------------------------------------------------

    def export_usvo(self, query: str = "", filters: dict | None = None) -> bytes:
        return export.usvo_xlsx(self._filtered_records(query, filters))

    def export_appeals(self) -> bytes:
        return export.appeals_xlsx(self.list_appeals())

    def export_applications(self) -> bytes:
        return export.applications_xlsx(self.list_applications())

    def export_analytics(self) -> bytes:
        return export.analytics_xlsx(self.analytics())

    # ---- заявления (мера поддержки по фото) --------------------------------

    def _application_to_dict(self, row) -> dict:
        d = dict(row)
        try:
            raw = json.loads(d.get("data") or "{}")
        except Exception:  # noqa: BLE001
            raw = {}
        data = normalize_application(raw)
        status = d.get("status") or "proposed"
        usvo = self._match_usvo(d.get("user_name") or "", "")
        result = {
            "id": d["id"],
            "measure_key": d.get("measure_key") or data.get("measure_key"),
            "measure_title": d.get("measure_title") or data.get("measure_title"),
            "status": status,
            "status_label": APP_STATUS_LABELS.get(status, status),
            "citizen": {
                "name": d.get("user_name") or data["applicant"].get("fio") or "",
                "username": d.get("username") or "",
            },
            "applicant": data["applicant"],
            "category": data["category"],
            "category_code": data["category_code"],
            "ownership": data["ownership"],
            "rooms": data["rooms"],
            "family": data["family"],
            "providers": data["providers"],
            "payment": data["payment"],
            "missing": data["missing"],
            "summary": application_summary(data),
            "decided_by": d.get("decided_by") or "",
            "usvo_id": usvo.id if usvo else None,
            "created_at": d.get("created_at") or 0,
            "created_human": _human_ts(d.get("created_at") or 0),
            "updated_human": _human_ts(d.get("updated_at") or 0),
            "downloadable": status in ("submitted", "approved", "awaiting_confirm"),
            "is_measure": False,
            "measure_fields": [],
            "documents": [],
            "user_files": raw.get("user_files") or [],
        }
        # Заявления нового сценария «Меры поддержки» — поля произвольные (по шаблону).
        if d.get("support_measure_id") or "fields" in raw:
            labels: dict[str, str] = {}
            sm_id = int(d.get("support_measure_id") or 0)
            if sm_id:
                m_row = self.store.get_support_measure(sm_id)
                if m_row:
                    md = measures_mod.measure_to_dict(m_row)
                    labels = {p["key"]: p["label"] for p in md["placeholders"]}
            fields = [
                {"label": labels.get(k, k), "value": v}
                for k, v in (raw.get("fields") or {}).items() if v
            ]
            summ = [f"📄 {result['measure_title']}"] + [
                f"{f['label']}: {f['value']}" for f in fields
            ]
            if result["user_files"]:
                summ.append(f"Прикреплено файлов: {len(result['user_files'])}")
            result["is_measure"] = True
            result["measure_fields"] = fields
            result["documents"] = raw.get("documents") or []
            result["summary"] = "\n".join(summ)
        return result

    def list_applications(self) -> list[dict]:
        # В админ-разделе показываем только то, что гражданин подтвердил и подал.
        rows = self.store.list_applications()
        return [
            self._application_to_dict(r) for r in rows
            if (r["status"] if not isinstance(r, dict) else r.get("status")) in
            ("submitted", "approved", "rejected")
        ]

    def get_application(self, application_id: int) -> dict | None:
        row = self.store.get_application(application_id)
        return self._application_to_dict(row) if row else None

    def decide_application(self, application_id: int, decision: str, operator: str) -> dict:
        row = self.store.get_application(application_id)
        if not row:
            return {"ok": False}
        self.store.set_application_status(application_id, decision, operator or "Администрация")
        # Уведомить заявителя в MAX — best-effort, не блокируя ответ кабинету.
        chat_id = row["user_chat_id"]
        token = (self.cfg.max.bot_token or "").lower()
        if chat_id and token and "xxxx" not in token:
            verb = "одобрено" if decision == "approved" else "отклонено"
            tail = ("Ожидайте назначения выплаты." if decision == "approved"
                    else "Свяжитесь с оператором для уточнения деталей.")
            msg = f"Ваше заявление «{row['measure_title']}» {verb} специалистом администрации. {tail}"
            self._send_max_async(msg, chat_id)
        return {"ok": True, "application": self.get_application(application_id)}

    def _send_max_async(self, text: str, chat_id: str) -> None:
        """Планирует отправку сообщения в MAX из работающего event loop (best-effort)."""
        import asyncio
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        loop.create_task(self.max_client.send_message(text, chat_id=chat_id))

    def delete_application(self, application_id: int) -> dict:
        return {"ok": self.store.delete_application(application_id)}

    def application_docx(self, application_id: int) -> bytes | None:
        row = self.store.get_application(application_id)
        if not row:
            return None
        d = dict(row)
        try:
            raw = json.loads(d.get("data") or "{}")
        except Exception:  # noqa: BLE001
            raw = {}
        # Заявление меры поддержки — заполняем загруженный шаблон офлайн.
        sm_id = int(d.get("support_measure_id") or 0)
        if sm_id:
            m_row = self.store.get_support_measure(sm_id)
            if m_row:
                md = measures_mod.measure_to_dict(m_row)
                path = md["template_path"]
                if path and os.path.exists(path):
                    try:
                        with open(path, "rb") as f:
                            return fill_template_docx(f.read(), raw.get("fields") or {})
                    except OSError:
                        pass
        return build_application_docx(normalize_application(raw))

    def _count_submitted_applications(self) -> int:
        return sum(
            1 for r in self.store.list_applications()
            if (r["status"] if not isinstance(r, dict) else r.get("status"))
            in ("submitted", "approved")
        )

    # ---- аналитика ---------------------------------------------------------

    def analytics(self) -> dict:
        appeals = self.list_appeals()
        appeal_times = [a["created_at"] for a in appeals if a["created_at"]]
        appeal_topics = [a["topic"] for a in appeals]
        if self._use_seeded_appeals():
            appointment_count = self._seed_appointments
        else:
            appointment_count = len(self._list_appointments())
        return build_analytics(
            self._all_records(), appeal_times, appeal_topics,
            appointment_count, self.cfg.web.contact_stale_days,
            applications=self._count_submitted_applications(),
        )

    # ---- база знаний (Dify Dataset) ---------------------------------------

    async def kb_upload_text(self, title: str, text: str) -> dict:
        """Загружает текстовый фрагмент в ту же базу знаний, куда пишутся ответы."""
        if not self.dify.kb_ready():
            return {"ok": False, "error": "База знаний Dify не настроена."}
        if not (text or "").strip():
            return {"ok": False, "error": "Пустой текст."}
        name = (title or "").strip() or f"Материал кабинета {_human_ts(time.time())}"
        try:
            res = await self.dify.add_document_text(name, text.strip())
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "error": f"Ошибка загрузки: {exc}"}
        if res.get("skipped"):
            return {"ok": False, "error": "База знаний Dify не настроена."}
        return {"ok": True, "name": name}

    async def kb_upload_file(self, filename: str, content: bytes) -> dict:
        """Загружает файл (txt/md/pdf/docx/…) в базу знаний Dify."""
        if not self.dify.kb_ready():
            return {"ok": False, "error": "База знаний Dify не настроена."}
        if not content:
            return {"ok": False, "error": "Пустой файл."}
        try:
            res = await self.dify.add_document_file(filename or "document", content)
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "error": f"Ошибка загрузки: {exc}"}
        if res.get("skipped"):
            return {"ok": False, "error": "База знаний Dify не настроена."}
        return {"ok": True, "name": filename}

    # ---- меры поддержки (CRUD из админ-раздела «Заявления») ----------------

    def list_support_measures(self) -> list[dict]:
        return measures_mod.all_measures(self.store)

    def get_support_measure(self, measure_id: int) -> dict | None:
        row = self.store.get_support_measure(measure_id)
        return measures_mod.measure_to_dict(row) if row else None

    def create_support_measure(self, payload: dict) -> dict:
        title = (payload.get("title") or "").strip()
        if not title:
            return {"ok": False, "error": "Укажите название меры поддержки."}
        data = measures_mod.build_measure_data(
            payload.get("documents"), payload.get("placeholders"),
            payload.get("llm_hint", ""), payload.get("category", ""),
        )
        measure_id = self.store.add_support_measure({
            "title": title,
            "description": (payload.get("description") or "").strip(),
            "data": json.dumps(data, ensure_ascii=False),
            "template_path": "",
            "active": bool(payload.get("active", True)),
        })
        return {"ok": True, "measure": self.get_support_measure(measure_id)}

    def update_support_measure(self, measure_id: int, payload: dict) -> dict:
        existing = self.get_support_measure(measure_id)
        if not existing:
            return {"ok": False, "error": "Мера поддержки не найдена."}
        title = (payload.get("title") or "").strip() or existing["title"]
        data = measures_mod.build_measure_data(
            payload.get("documents", existing["documents"]),
            payload.get("placeholders", existing["placeholders"]),
            payload.get("llm_hint", existing["llm_hint"]),
            payload.get("category", existing["category"]),
        )
        self.store.update_support_measure(measure_id, {
            "title": title,
            "description": (payload.get("description") if payload.get("description") is not None
                           else existing["description"]).strip(),
            "data": json.dumps(data, ensure_ascii=False),
            "template_path": existing["template_path"],
            "active": bool(payload.get("active", existing["active"])),
        })
        return {"ok": True, "measure": self.get_support_measure(measure_id)}

    def delete_support_measure(self, measure_id: int) -> dict:
        existing = self.get_support_measure(measure_id)
        if existing and existing["template_path"]:
            try:
                if os.path.exists(existing["template_path"]):
                    os.remove(existing["template_path"])
            except OSError:
                pass
        ok = self.store.delete_support_measure(measure_id)
        return {"ok": ok}

    def save_measure_template(self, measure_id: int, filename: str, content: bytes) -> dict:
        """Сохраняет загруженный .docx-шаблон, извлекает плейсхолдеры и привязывает их."""
        existing = self.get_support_measure(measure_id)
        if not existing:
            return {"ok": False, "error": "Мера поддержки не найдена."}
        if not content:
            return {"ok": False, "error": "Пустой файл."}
        keys = extract_placeholders(content)
        tpl_dir = self.cfg.dify.measure_templates_dir
        try:
            os.makedirs(tpl_dir, exist_ok=True)
            path = os.path.join(tpl_dir, f"{measure_id}.docx")
            with open(path, "wb") as f:
                f.write(content)
        except OSError as exc:
            return {"ok": False, "error": f"Не удалось сохранить шаблон: {exc}"}

        # Объединяем найденные плейсхолдеры с уже заданными (подписи сохраняем).
        existing_ph = {p["key"]: p["label"] for p in existing["placeholders"]}
        merged = [{"key": k, "label": existing_ph.get(k, measures_mod.humanize_key(k))}
                  for k in keys]
        # Добавляем ранее заданные ключи, которых нет в новом шаблоне (на всякий случай).
        for p in existing["placeholders"]:
            if p["key"] not in {m["key"] for m in merged}:
                merged.append(p)
        data = measures_mod.build_measure_data(
            existing["documents"], merged, existing["llm_hint"], existing["category"],
        )
        self.store.update_support_measure(measure_id, {
            "title": existing["title"],
            "description": existing["description"],
            "data": json.dumps(data, ensure_ascii=False),
            "template_path": path,
            "active": existing["active"],
        })
        return {"ok": True, "placeholders": merged, "template_name": filename or "template.docx"}

    def get_measure_template(self, measure_id: int) -> tuple[bytes, str] | None:
        m = self.get_support_measure(measure_id)
        if not m or not m["template_path"] or not os.path.exists(m["template_path"]):
            return None
        with open(m["template_path"], "rb") as f:
            return f.read(), os.path.basename(m["template_path"])

    # Имя документа меры в базе знаний — стабильный префикс с id, чтобы повторная
    # синхронизация обновляла тот же документ, а не плодила дубли.
    @staticmethod
    def _measure_doc_prefix(measure_id: int) -> str:
        return f"Мера поддержки #{measure_id}:"

    def _measure_doc_name(self, m: dict) -> str:
        return f"{self._measure_doc_prefix(m['id'])} {m['title']}".strip()

    async def sync_measure_to_kb(self, measure_id: int) -> dict:
        """Автосинхронизация одной меры в базу знаний (вызывается после правок).

        Активная мера — создаётся/обновляется отдельным документом; неактивная или
        удалённая — убирается из базы знаний. Никогда не бросает исключение
        (best-effort, чтобы не ломать CRUD).
        """
        if not self.dify.kb_ready():
            return {"ok": False, "error": "База знаний Dify не настроена."}
        m = self.get_support_measure(measure_id)
        if not m:
            return await self.remove_measure_from_kb(measure_id)
        if not m["active"]:
            return await self.remove_measure_from_kb(measure_id)
        try:
            res = await self.dify.upsert_document_text(
                self._measure_doc_name(m),
                measures_mod.one_measure_text(m),
                match_prefix=self._measure_doc_prefix(measure_id),
            )
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "error": f"Ошибка синхронизации: {exc}"}
        if res.get("skipped"):
            return {"ok": False, "error": "База знаний Dify не настроена."}
        return {"ok": True}

    async def remove_measure_from_kb(self, measure_id: int) -> dict:
        """Убирает документ меры из базы знаний (при удалении/деактивации)."""
        if not self.dify.kb_ready():
            return {"ok": False, "error": "База знаний Dify не настроена."}
        try:
            await self.dify.delete_documents_by_prefix(self._measure_doc_prefix(measure_id))
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "error": f"Ошибка удаления из базы знаний: {exc}"}
        return {"ok": True}

    async def sync_measures_kb(self) -> dict:
        """Полная пересинхронизация всех мер с базой знаний (кнопка «Синхронизировать»)."""
        if not self.dify.kb_ready():
            return {"ok": False, "error": "База знаний Dify не настроена."}
        count = 0
        for m in measures_mod.all_measures(self.store):
            if m["active"]:
                await self.sync_measure_to_kb(m["id"])
                count += 1
            else:
                await self.remove_measure_from_kb(m["id"])
        return {"ok": True, "count": count}

    def meta(self) -> dict:
        web_ai = getattr(self.cfg.web, "ai", None)
        web_ai_provider = getattr(web_ai, "provider", "local") if web_ai else "local"
        web_ai_ready = ai.dify_ready(self.cfg)
        from app.web.history_ai import history_dify_ready
        usvo_knowledge = self.usvo_knowledge
        return {
            "title": self.cfg.web.title,
            "operators": self._operators(),
            "usvo_error": self.usvo.error,
            "dify_ready": web_ai_ready,
            "web_ai_provider": web_ai_provider,
            "web_ai_ready": web_ai_ready,
            "seeded_appeals": self._use_seeded_appeals(),
            "uploaded_usvo": self.usvo_db.count(),
            "history_ai_ready": history_dify_ready(self.cfg),
            "usvo_statuses": self.usvo_statuses(),
            "kb_ready": self.dify.kb_ready(),
            "usvo_ai_ready": bool(usvo_knowledge and usvo_knowledge.dify.app_ready()),
            "usvo_ai_kb_ready": bool(usvo_knowledge and usvo_knowledge.ready()),
        }


def _human_ts(ts: float) -> str:
    if not ts:
        return "—"
    return dt.datetime.fromtimestamp(ts).strftime("%d.%m.%Y %H:%M")


def _tri(want, actual: bool) -> bool:
    """Трёхпозиционный фильтр (yes/no/любой). Возвращает True, если запись надо
    ОТСЕЯТЬ. `want` ∈ {"yes","no",""/None}. Пустое значение — фильтр выключен."""
    w = (want or "").strip().lower()
    if w in ("yes", "да", "true", "1"):
        return not bool(actual)
    if w in ("no", "нет", "false", "0"):
        return bool(actual)
    return False
