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
import logging
import os
import random
import re
import sqlite3
import time

from app.common.timez import human_ts
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
from app.web.sla import sla_fields
from app.web.topics import classify_topic
from app.web.usvo import (
    UsvoRecord,
    UsvoStore,
    _norm_birth,
    _norm_name,
    _norm_phone,
    card_identity,
    card_identity_strict,
    find_field_value,
    record_identity,
    record_identity_strict,
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

    def _max_ready(self) -> bool:
        """MAX-бот настроен реальным токеном (не плейсхолдер) — можно слать сообщения."""
        token = (self.cfg.max.bot_token or "").strip()
        return bool(token) and "xxxx" not in token.lower()

    def _safe_escalation_event(self, escalation_id: int, kind: str, detail: str = "") -> None:
        """Best-effort запись события в хронологию обращения (не ломает основное действие)."""
        try:
            if hasattr(self.store, "add_escalation_event"):
                self.store.add_escalation_event(escalation_id, kind, detail)
        except Exception:  # noqa: BLE001
            import logging
            logging.getLogger("web").warning(
                "Не удалось записать событие обращения #%s (%s)", escalation_id, kind
            )

    def _audit(self, action: str, *, actor: dict | None = None, entity: str = "",
               entity_id: str = "", details: str = "") -> None:
        """Best-effort запись в аудит-лог. Ошибка аудита логируется, но НЕ ломает
        основное действие (требование госсектора: действие важнее записи следа)."""
        try:
            if not hasattr(self.store, "add_audit_log"):
                return
            actor = actor or {}
            self.store.add_audit_log(
                action,
                user_sub=str(actor.get("sub") or ""),
                user_name=str(actor.get("name") or ""),
                entity=entity, entity_id=str(entity_id), details=details,
            )
        except Exception:  # noqa: BLE001
            import logging
            logging.getLogger("web").warning("Не удалось записать аудит-лог: %s", action)

    def list_audit(self, limit: int = 500, entity: str = "", action: str = "") -> list[dict]:
        if not hasattr(self.store, "list_audit_log"):
            return []
        rows = self.store.list_audit_log(limit=limit, entity=entity, action=action)
        out: list[dict] = []
        for r in rows:
            d = dict(r)
            out.append({
                "id": d.get("id"),
                "user_sub": d.get("user_sub") or "",
                "user_name": d.get("user_name") or "",
                "action": d.get("action") or "",
                "entity": d.get("entity") or "",
                "entity_id": d.get("entity_id") or "",
                "details": d.get("details") or "",
                "at": d.get("at") or 0,
                "at_human": _human_ts(d.get("at") or 0),
            })
        return out

    # ---- регламент SLA (редактируется администратором) --------------------

    SLA_DAYS_MIN = 1
    SLA_DAYS_MAX = 30

    def sla_business_days(self) -> int:
        """Текущий регламент ответа (календарных дней, считаются все дни подряд).
        Значение, заданное админом в «Настройках» (таблица app_settings), имеет
        приоритет над дефолтом из YAML — меняется без правки конфига и перезапуска."""
        default = int(self.cfg.web.sla_business_days)
        if not hasattr(self.store, "get_setting"):
            return default
        raw = self.store.get_setting("sla_business_days")
        if raw is None:
            return default
        try:
            return self._clamp_sla_days(int(raw))
        except (TypeError, ValueError):
            return default

    @classmethod
    def _clamp_sla_days(cls, days: int) -> int:
        return max(cls.SLA_DAYS_MIN, min(cls.SLA_DAYS_MAX, int(days)))

    def get_sla_settings(self) -> dict:
        return {
            "sla_business_days": self.sla_business_days(),
            "default_business_days": int(self.cfg.web.sla_business_days),
            "min": self.SLA_DAYS_MIN,
            "max": self.SLA_DAYS_MAX,
        }

    def set_sla_business_days(self, days: int, *, actor: dict | None = None) -> dict:
        """Сохраняет регламент ответа. Значение зажимается в [1..30]."""
        try:
            value = self._clamp_sla_days(int(days))
        except (TypeError, ValueError):
            raise ValueError("Укажите число дней") from None
        if not hasattr(self.store, "set_setting"):
            raise ValueError("Хранилище настроек недоступно")
        self.store.set_setting("sla_business_days", str(value))
        self._audit("sla_update", actor=actor, entity="settings",
                    entity_id="sla_business_days",
                    details=f"Регламент ответа: {value} дн.")
        return self.get_sla_settings()

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

    def _name_tokens_match(self, name: str, rec: UsvoRecord) -> bool:
        """Строгий матч ФИО для связи обращения: минимум ДВА общих значимых токена
        (длиной ≥3, напр. фамилия + имя). Одно общее имя («Владислав») карточку НЕ
        связывает — иначе была ложная привязка к чужому «Зорин Владислав …»."""
        tokens = {t for t in _norm_name(name).split() if len(t) >= 3}
        if len(tokens) < 2:
            return False
        rt = {t for t in _norm_name(rec.name).split() if len(t) >= 3}
        return len(tokens & rt) >= 2

    def _link_usvo(self, name: str = "", phone: str = "", birth: str = "") -> dict:
        """Определяет связь обращения из MAX с карточкой(ами) УСВО.

        Каскад поиска (ФИО обязательно на КАЖДОМ шаге; берётся первый шаг, давший
        совпадения):
          1) ФИО + телефон + дата рождения;
          2) ФИО + телефон  ИЛИ  ФИО + дата рождения;
          3) только ФИО.
        (Дата рождения из MAX сейчас недоступна — тогда шаги 1 и «+дата» пусты, и
        каскад корректно вырождается в ФИО+телефон → ФИО.)

        Возвращает {usvo_id, usvo_matches:[{id,name,phone}], usvo_ambiguous,
        link_by}. `link_by` ∈ {"phone","name",""}: если во всех совпадениях
        подтверждён телефон — «phone» (надёжно), иначе «name» (предположение —
        UI просит проверить номер, чтобы не выдавать матч по ФИО/дате за «по
        номеру телефона»)."""
        qphone = _norm_phone(phone)
        qbirth = _norm_birth(birth)

        def phone_ok(r: UsvoRecord) -> bool:
            return bool(qphone) and _norm_phone(r.phone) == qphone

        def birth_ok(r: UsvoRecord) -> bool:
            return bool(qbirth) and _norm_birth(r.birth_date) == qbirth

        # ФИО — обязательное условие связи на всех шагах каскада.
        named = [r for r in self._all_records() if self._name_tokens_match(name, r)]
        matches = [r for r in named if phone_ok(r) and birth_ok(r)]          # 1
        if not matches:
            matches = [r for r in named if phone_ok(r) or birth_ok(r)]       # 2
        if not matches:
            matches = named                                                  # 3

        if not matches:
            return {"usvo_id": None, "usvo_matches": [], "usvo_ambiguous": False,
                    "link_by": ""}
        # Телефон подтверждён во ВСЕХ совпадениях → надёжная связь «по телефону»,
        # иначе (совпадение только по ФИО/дате) — мягкая «по ФИО».
        link_by = "phone" if all(phone_ok(r) for r in matches) else "name"
        return {
            "usvo_id": matches[0].id if len(matches) == 1 else None,
            "usvo_matches": [{"id": r.id, "name": r.name, "phone": r.phone} for r in matches],
            "usvo_ambiguous": len(matches) > 1,
            "link_by": link_by,
        }

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

    def _escalation_phone(self, d: dict) -> str:
        """Телефон гражданина по обращению: из столбца escalations.phone (новые
        обращения) либо из профиля пользователя MAX (users.phone) — для старых."""
        phone = (d.get("phone") or "").strip()
        if phone:
            return phone
        user_id = d.get("user_id") or ""
        if user_id:
            urow = self.store.get_user(user_id)
            if urow:
                return (dict(urow).get("phone") or "").strip()
        return ""

    def _escalation_to_appeal(self, row) -> dict:
        d = dict(row)
        question = d.get("question") or ""
        topic = d.get("topic") or classify_topic(question)
        phone = self._escalation_phone(d)
        link = self._link_usvo(d.get("user_name") or "", phone)
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
                "phone": phone,
                "username": d.get("username") or "",
                "user_id": d.get("user_id") or "",
            },
            "usvo_id": link["usvo_id"],
            "usvo_matches": link["usvo_matches"],
            "usvo_ambiguous": link["usvo_ambiguous"],
            "link_by": link["link_by"],
        }

    def list_appeals(self) -> list[dict]:
        if self._use_seeded_appeals():
            appeals = list(self._seeded_appeals())
        else:
            appeals = [self._escalation_to_appeal(r) for r in self._list_escalations()]
        sla_days = self.sla_business_days()
        for a in appeals:
            a["created_human"] = _human_ts(a["created_at"])
            # Лёгкие ИИ-инсайты для таблицы: тональность (смайл-индикатор) и
            # «суть кратко» в одну строку. Детерминированно, без обращения к LLM.
            a["sentiment"] = analyze_sentiment(a.get("question", ""))
            a["summary"] = summarize(a.get("question", ""))
            # SLA: возраст обращения и признак просрочки (устойчиво к пустому created_at).
            sla = sla_fields(a.get("created_at") or 0, business_days=sla_days,
                             status=a.get("status", "open"))
            a["age"] = sla["age"]
            a["age_days"] = sla["age_days"]
            a["deadline_at"] = sla["deadline_at"]
            a["deadline_human"] = _human_ts(sla["deadline_at"]) if sla["deadline_at"] else "—"
            a["is_overdue"] = sla["is_overdue"]
            # Совместимость: у seed-обращений может не быть новых полей связи.
            a.setdefault("usvo_matches", [{"id": a["usvo_id"], "name": (a.get("citizen") or {}).get("name") or ""}] if a.get("usvo_id") else [])
            a.setdefault("usvo_ambiguous", False)
            # seed-обращения строятся из самой карточки (телефон гражданина = телефон
            # карточки) — их связь корректно считать «по телефону».
            a.setdefault("link_by", "phone" if a.get("usvo_id") else "")
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
        events: list[dict] = []

        if appeal.get("real") and user_id:
            urow = self.store.get_user(user_id)
            if urow:
                u = dict(urow)
                profile = {
                    "user_id": user_id,
                    "name": u.get("name") or "",
                    "username": u.get("username") or "",
                    "phone": u.get("phone") or (appeal.get("citizen") or {}).get("phone") or "",
                    "question_count": u.get("question_count") or 0,
                    "subscribed": bool(u.get("subscribed")),
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
            # Хронология изменений текущего обращения (создание/статус/уведомления).
            if appeal_id.startswith("esc-") and hasattr(self.store, "list_escalation_events"):
                try:
                    for ev in self.store.list_escalation_events(int(appeal_id[4:])):
                        e = dict(ev)
                        events.append({
                            "kind": e.get("kind") or "",
                            "detail": e.get("detail") or "",
                            "created_human": _human_ts(e.get("created_at") or 0),
                        })
                except Exception:  # noqa: BLE001
                    events = []
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

        return {"profile": profile, "items": items, "count": len(items), "events": events}

    async def answer(self, appeal_id: str, answer_text: str, assignee: str,
                     actor: dict | None = None) -> dict:
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
            if row and row["user_id"] and self._max_ready():
                try:
                    await self.max_client.send_message(answer_text, user_id=row["user_id"])
                    delivered = True
                except Exception:  # noqa: BLE001
                    delivered = False
            # Событие хронологии: доставлено ли уведомление гражданину.
            self._safe_escalation_event(
                esc_id, "notification",
                "Ответ отправлен гражданину в MAX" if delivered
                else "Ответ сохранён (уведомление в MAX не отправлено)",
            )
        else:
            for a in self._seeded_appeals():
                if a["id"] == appeal_id:
                    a["status"] = "answered"
                    a["answer"] = answer_text
                    if assignee:
                        a["assignee"] = assignee

        self._audit("answer_appeal", actor=actor, entity="appeal", entity_id=appeal_id,
                    details=f"Ответ на обращение · доставлен={delivered}")
        return {"ok": True, "delivered_to_citizen": delivered, "saved_to_kb": kb_saved,
                "appeal": self.get_appeal(appeal_id)}

    def delete_appeal(self, appeal_id: str, actor: dict | None = None) -> dict:
        if appeal_id.startswith("esc-"):
            deleted = self._delete_escalation(int(appeal_id[4:]))
            if deleted:
                self._audit("delete_appeal", actor=actor, entity="appeal", entity_id=appeal_id)
            return {"ok": deleted}
        before = len(self._seeded_appeals())
        self._seed_appeals = [a for a in self._seeded_appeals() if a["id"] != appeal_id]
        ok = len(self._seed_appeals) < before
        if ok:
            self._audit("delete_appeal", actor=actor, entity="appeal", entity_id=appeal_id)
        return {"ok": ok}

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

    async def import_usvo(self, file_bytes: bytes, replace: bool = False,
                          actor: dict | None = None) -> dict:
        """Парсит загруженный Excel, нормализует историю взаимодействия и сохраняет."""
        try:
            cards = parse_usvo_xlsx(file_bytes)
        except Exception as exc:  # noqa: BLE001
            # Техническую причину (BadZipFile, InvalidFileException и т.п.) прячем в
            # лог — оператору показываем понятную подсказку без внутренностей.
            import logging
            logging.getLogger("web").warning(
                "Импорт УСВО: не удалось прочитать файл: %s: %s",
                type(exc).__name__, exc,
            )
            return {"ok": False, "error": "Не удалось прочитать файл. Убедитесь, что "
                    "это корректная таблица Excel (.xlsx) и файл не повреждён."}
        if not cards:
            return {"ok": False, "error": "В файле не найдено ни одной карточки."}

        if replace:
            self.store.clear_usvo_cards()

        # Идентичности уже имеющихся карточек (загруженных + табличных). Совпадающие
        # карточки из файла пропускаем — это и есть защита от дубликатов: версия,
        # лежащая в базе (с правками оператора), остаётся, копия из Excel не плодится.
        # ЗАГРУЗКА данных: дубль = совпали ВСЕ три реквизита сразу (ФИО + дата
        # рождения + телефон) — строгий ключ `card_identity_strict`.
        existing = {record_identity_strict(r) for r in self._all_records()}
        existing.discard("")

        import time as _t
        batch = f"import-{int(_t.time())}"
        saved = 0
        saved_ids: list[int] = []
        skipped = 0
        # Подсказки оператору: какие именно записи пропущены как дубли и по каким
        # идентификаторам они распознаны (ФИО + дата рождения / телефон).
        skipped_details: list[dict] = []
        for card in cards:
            ident = card_identity_strict(card.get("name", ""), card.get("birth_date", ""),
                                         card.get("phone", ""))
            if ident and ident in existing:
                skipped += 1
                skipped_details.append({
                    "name": card.get("name", "") or "(без ФИО)",
                    "birth_date": card.get("birth_date", ""),
                    "phone": card.get("phone", ""),
                    "reason": _dedup_reason(ident),
                    "matched_by": _dedup_matched_by(card),
                })
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
        self._audit("import_usvo", actor=actor, entity="usvo_card",
                    details=f"Импорт Excel: сохранено {saved}, пропущено дублей {skipped}"
                            + (", с заменой" if replace else ""))
        return {
            "ok": True,
            "saved": saved,
            "skipped": skipped,
            "skipped_details": skipped_details,
            "replaced": replace,
            "total_uploaded": self.usvo_db.count(),
            "knowledge_sync": knowledge_sync,
        }

    def usvo_template(self) -> bytes:
        return build_template_xlsx()

    async def update_usvo(self, rec_id: int, fields: list[dict],
                          history_raw: str | None = None,
                          actor: dict | None = None) -> dict:
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
            self._audit("update_usvo", actor=actor, entity="usvo_card", entity_id=rec_id,
                        details=f"Обновлена карточка «{header.get('name') or ''}»")
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
        self._audit("update_usvo", actor=actor, entity="usvo_card", entity_id=new_id,
                    details=f"Отредактирована табличная карточка «{header.get('name') or ''}»")
        knowledge_sync = await self._sync_usvo_knowledge(
            card_ids=[new_id], removed_ids=[rec_id]
        )
        return {"ok": True, "id": new_id, "knowledge_sync": knowledge_sync}

    async def delete_usvo(self, rec_id: int, actor: dict | None = None) -> dict:
        if not is_db_id(rec_id):
            return {"ok": False, "error": "Табличные карточки удалять нельзя — только загруженные."}
        # ФИО удаляемой карточки читаем ДО удаления — чтобы в аудите (колонка «Детали»)
        # было видно, какую именно карточку удалили, а не пустое значение.
        row = self.store.get_usvo_card(rec_id - USVO_DB_ID_BASE)
        name = (dict(row).get("name") or "").strip() if row else ""
        ok = self.store.delete_usvo_card(rec_id - USVO_DB_ID_BASE)
        if not ok:
            return {"ok": False}
        self._audit("delete_usvo", actor=actor, entity="usvo_card", entity_id=rec_id,
                    details=f"Удалена карточка «{name}»" if name else "Удалена карточка УСВО")
        # После удаления оверрайда может снова проявиться одноимённая карточка из
        # основной Excel-таблицы, поэтому безопаснее пересобрать весь набор.
        knowledge_sync = await self._sync_usvo_knowledge(rebuild=True)
        return {"ok": True, "knowledge_sync": knowledge_sync}

    async def clear_uploaded_usvo(self, actor: dict | None = None) -> dict:
        deleted = self.store.clear_usvo_cards()
        self._audit("clear_usvo", actor=actor, entity="usvo_card",
                    details=f"Удалено загруженных карточек: {deleted}")
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

    def usvo_card_docx(self, rec_id: int) -> bytes | None:
        """Выгрузка одной карточки УСВО в .docx (для передачи в ведомства).

        Переиспользует офлайн-сборщик `common/docx.build_docx` — тот же механизм, что
        и справки чата/заявления. Пустые необязательные поля просто пропускаются.
        PDF пока не генерируем: точка расширения — конвертация этого .docx во внешнем
        сервисе (LibreOffice/докген-плагин), см. usvo_card_docx в router/CLAUDE.md."""
        r = self._get_record(rec_id)
        if not r:
            return None
        from app.common.docx import Paragraph, Table, build_docx

        blocks: list = [
            Paragraph("Персональная карточка участника СВО", bold=True, size=16, align="center"),
            Paragraph(f"Сформировано: {_human_ts(time.time())}", align="center"),
            Paragraph(""),
        ]
        main_rows = [["Показатель", "Значение"]]

        def _add(label: str, value: str) -> None:
            if (value or "").strip():
                main_rows.append([label, value])

        _add("ФИО", r.name)
        _add("Статус", r.status if r.status != "—" else "")
        _add("Дата рождения", r.birth_date)
        _add("Телефон", r.phone)
        _add("Адрес регистрации", r.address)
        _add("Дата обзвона", r.call_date)
        _add("Награды", r.awards)
        blocks.append(Paragraph("Основные сведения", bold=True, size=13))
        blocks.append(Table(main_rows, header=True))

        # Полный набор полей карточки (дедуп по названию), исключая пустые.
        seen: set[str] = set()
        field_rows: list[list[str]] = [["Поле", "Значение"]]
        for f in [*r.primary, *r.secondary, *r.extra]:
            label = (f.label or "").strip()
            value = (f.value or "").strip()
            key = label.lower()
            if not label or not value or key in seen:
                continue
            seen.add(key)
            field_rows.append([label, value])
        if len(field_rows) > 1:
            blocks.append(Paragraph(""))
            blocks.append(Paragraph("Данные участника", bold=True, size=13))
            blocks.append(Table(field_rows, header=True))

        if r.head_directive and (r.head_directive.get("text") or "").strip():
            blocks.append(Paragraph(""))
            blocks.append(Paragraph("Поручение Главы округа", bold=True, size=13))
            blocks.append(Paragraph(r.head_directive["text"]))

        history = r.history or []
        if history:
            blocks.append(Paragraph(""))
            blocks.append(Paragraph("История взаимодействия", bold=True, size=13))
            for e in history:
                head = " · ".join(
                    p for p in [e.get("date"), e.get("title"), e.get("status")] if p
                )
                if head:
                    blocks.append(Paragraph(f"• {head}", bold=True))
                if (e.get("detail") or "").strip():
                    blocks.append(Paragraph(f"    {e['detail']}"))
                if (e.get("org") or "").strip():
                    blocks.append(Paragraph(f"    {e['org']}"))
        return build_docx(blocks)

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

    def decide_application(self, application_id: int, decision: str, operator: str,
                           actor: dict | None = None) -> dict:
        row = self.store.get_application(application_id)
        if not row:
            return {"ok": False}
        self.store.set_application_status(application_id, decision, operator or "Администрация")
        # Уведомить заявителя в MAX — best-effort, не блокируя ответ кабинету.
        # Гражданин связан с заявлением через user_chat_id/user_id.
        chat_id = row["user_chat_id"]
        user_id = row["user_id"]
        notified = False
        if (chat_id or user_id) and self._max_ready():
            verb = "одобрено" if decision == "approved" else "отклонено"
            tail = ("Ожидайте назначения выплаты." if decision == "approved"
                    else "Свяжитесь с оператором для уточнения деталей.")
            msg = f"Ваше заявление «{row['measure_title']}» {verb} специалистом администрации. {tail}"
            notified = self._send_max_async(msg, chat_id, user_id)
        self._audit("decide_application", actor=actor, entity="application",
                    entity_id=application_id,
                    details=f"Статус → {decision}; уведомление гражданину={notified}")
        return {"ok": True, "application": self.get_application(application_id),
                "notified": notified}

    def _send_max_async(self, text: str, chat_id: str, user_id: str = "") -> bool:
        """Планирует отправку сообщения в MAX из работающего event loop (best-effort).

        Возвращает True, если отправка запланирована (доставку подтвердить синхронно
        нельзя). При отсутствии активного loop — False."""
        import asyncio
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return False
        loop.create_task(self.max_client.send_message(
            text, chat_id=chat_id or None, user_id=user_id or None))
        return True

    def delete_application(self, application_id: int, actor: dict | None = None) -> dict:
        ok = self.store.delete_application(application_id)
        if ok:
            self._audit("delete_application", actor=actor, entity="application",
                        entity_id=application_id)
        return {"ok": ok}

    # ---- push-рассылки пользователям MAX -----------------------------------

    def broadcast_audience(self) -> dict:
        """Сколько пользователей получит каждый тип рассылки (для UI)."""
        try:
            total = len(self.store.list_users())
            subscribed = len(self.store.list_users(subscribed_only=True))
        except Exception:  # noqa: BLE001
            total, subscribed = 0, 0
        return {"total": total, "subscribers": subscribed, "max_ready": self._max_ready()}

    async def broadcast(self, text: str, target: str = "all",
                        actor: dict | None = None) -> dict:
        """Массовая отправка сообщения пользователям MAX.

        target: "all" — всем, кто когда-либо писал боту; "subscribers" — только
        подписанным (users.subscribed=1). Ошибка отправки конкретному пользователю
        не останавливает рассылку (обрабатывается отдельно и логируется)."""
        text = (text or "").strip()
        if not text:
            return {"ok": False, "error": "Введите текст сообщения."}
        if not self._max_ready():
            return {"ok": False, "error": "MAX-бот не настроен — рассылка недоступна."}
        subscribed_only = (target == "subscribers")
        users = self.store.list_users(subscribed_only=subscribed_only)
        blog = logging.getLogger("web.broadcast")
        sent = 0
        failed = 0
        errors: list[dict] = []
        for row in users:
            d = dict(row)
            chat_id = d.get("chat_id") or ""
            user_id = d.get("user_id") or ""
            try:
                res = await self.max_client.send_message(
                    text, chat_id=chat_id or None, user_id=user_id or None
                )
                if isinstance(res, dict) and res.get("ok") is False:
                    raise RuntimeError("MAX отклонил отправку")
                sent += 1
            except Exception as exc:  # noqa: BLE001 — одна ошибка не рушит рассылку
                failed += 1
                errors.append({"user_id": user_id, "error": str(exc)})
                blog.warning("Рассылка: не доставлено пользователю %s: %s", user_id, exc)
        blog.info("Рассылка (target=%s): аудитория=%d, доставлено=%d, ошибок=%d",
                  target, len(users), sent, failed)
        self._audit("broadcast", actor=actor, entity="broadcast",
                    details=f"target={target} аудитория={len(users)} доставлено={sent} ошибок={failed}")
        return {
            "ok": True,
            "target": target,
            "total": len(users),
            "sent": sent,
            "failed": failed,
            "errors": errors[:100],
            # Актуальная аудитория ПОСЛЕ рассылки — чтобы кабинет обновил счётчики
            # «подписчиков»/«всех» без ручного рефреша (иначе он показывал бы число,
            # каким оно было при открытии страницы, до отписок пользователей).
            "audience": self.broadcast_audience(),
        }

    async def notify_subscribers(self, text: str, actor: dict | None = None) -> dict:
        """Отправляет сообщение всем подписанным пользователям (users.subscribed=1)."""
        return await self.broadcast(text, target="subscribers", actor=actor)

    async def broadcast_all(self, text: str, actor: dict | None = None) -> dict:
        """Отправляет сообщение всем пользователям, когда-либо писавшим боту."""
        return await self.broadcast(text, target="all", actor=actor)

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
        """Полная пересинхронизация всех мер с базой знаний (кнопка «Синхронизировать».)

        Помимо создания/обновления документов активных мер УДАЛЯЕТ документы-призраки:
        меры, которых больше нет в БД (например, удалённые или оставшиеся от прежней
        БД с другими id). Иначе в базе знаний накапливаются осиротевшие документы
        «Мера поддержки #N», и ассистент подбора (measure_assistant.yml) возвращает
        measure_id, которого нет в БД, — предложение меры молча не срабатывает
        (см. bot_logic: measure по id не находится → оффер не показывается)."""
        if not self.dify.kb_ready():
            return {"ok": False, "error": "База знаний Dify не настроена."}
        measures = measures_mod.all_measures(self.store)
        active_ids = {int(m["id"]) for m in measures if m["active"]}
        count = 0
        for m in measures:
            if m["active"]:
                await self.sync_measure_to_kb(m["id"])
                count += 1
            else:
                await self.remove_measure_from_kb(m["id"])
        removed_orphans = await self._purge_orphan_measure_docs(active_ids)
        return {"ok": True, "count": count, "removed_orphans": removed_orphans}

    async def _purge_orphan_measure_docs(self, keep_ids: set[int]) -> int:
        """Удаляет из базы знаний документы мер, чьих id нет среди keep_ids.

        Документ меры называется «Мера поддержки #<id>: …»; из имени вынимаем id и
        удаляем всё, что не относится к текущим активным мерам (best-effort — ошибка
        по одному документу не срывает пересинхронизацию)."""
        try:
            docs = await self.dify.list_all_documents(keyword="Мера поддержки")
        except Exception:  # noqa: BLE001
            return 0
        removed = 0
        for d in docs:
            name = d.get("name") or ""
            match = re.match(r"Мера поддержки #(\d+):", name)
            if not match:
                continue
            if int(match.group(1)) in keep_ids:
                continue
            doc_id = d.get("id")
            if not doc_id:
                continue
            try:
                await self.dify.delete_document(doc_id)
                removed += 1
            except Exception:  # noqa: BLE001
                pass
        return removed

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
            "sla_business_days": self.sla_business_days(),
            "max_ready": self._max_ready(),
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
    return human_ts(ts)


def _dedup_reason(ident: str) -> str:
    """Человекочитаемая причина, почему запись распознана как дубль (по префиксу
    ключа идентичности). При загрузке используется строгий ключ `npb`
    (ФИО + дата рождения + телефон), остальные — на случай старых вызовов."""
    prefix = ident.split(":", 1)[0] if ":" in ident else ""
    return {
        "npb": "Совпали ФИО, дата рождения и телефон",
        "nb": "Совпали ФИО и дата рождения",
        "np": "Совпали ФИО и телефон",
        "n": "Совпало ФИО",
        "p": "Совпал телефон",
    }.get(prefix, "Найдена уже существующая карточка")


def _dedup_matched_by(card: dict) -> list[str]:
    """Список идентификаторов, по которым запись сопоставлена (для подсказки)."""
    out: list[str] = []
    if (card.get("name") or "").strip():
        out.append("ФИО")
    if (card.get("birth_date") or "").strip():
        out.append("дата рождения")
    if (card.get("phone") or "").strip():
        out.append("телефон")
    return out


def _tri(want, actual: bool) -> bool:
    """Трёхпозиционный фильтр (yes/no/любой). Возвращает True, если запись надо
    ОТСЕЯТЬ. `want` ∈ {"yes","no",""/None}. Пустое значение — фильтр выключен."""
    w = (want or "").strip().lower()
    if w in ("yes", "да", "true", "1"):
        return not bool(actual)
    if w in ("no", "нет", "false", "0"):
        return bool(actual)
    return False
