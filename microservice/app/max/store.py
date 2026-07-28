"""SQLite-хранилище состояния MAX-бота.

Хранит то, что нельзя держать в Dify (stateful-диалог):
  - users          : пользователи и счётчик заданных вопросов;
  - questions       : история вопросов (для "последних трёх");
  - escalations     : вопросы, переданные операторам;
  - operator_state  : какой оператор сейчас отвечает на какую эскалацию;
  - appointments    : выбранные слоты записи на приём.

Используется только stdlib sqlite3. Соединение открывается на каждую операцию —
для нагрузки бота это с запасом достаточно и безопасно при нескольких воркерах.
"""
from __future__ import annotations

import os
import sqlite3
import time
from contextlib import contextmanager


class Store:
    def __init__(self, path: str):
        self.path = path
        directory = os.path.dirname(path)
        if directory:
            os.makedirs(directory, exist_ok=True)
        self._init_db()

    @contextmanager
    def _conn(self):
        conn = sqlite3.connect(self.path, timeout=10)
        conn.row_factory = sqlite3.Row
        try:
            conn.execute("PRAGMA journal_mode=WAL;")
            conn.execute("PRAGMA foreign_keys=ON;")
            yield conn
            conn.commit()
        finally:
            conn.close()

    def _init_db(self) -> None:
        with self._conn() as c:
            c.executescript(
                """
                CREATE TABLE IF NOT EXISTS users (
                    user_id      TEXT PRIMARY KEY,
                    chat_id      TEXT,
                    name         TEXT,
                    username     TEXT,
                    phone        TEXT,
                    question_count INTEGER NOT NULL DEFAULT 0,
                    questions_since_offer INTEGER NOT NULL DEFAULT 0
                );

                CREATE TABLE IF NOT EXISTS questions (
                    id        INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id   TEXT NOT NULL,
                    text      TEXT NOT NULL,
                    created_at REAL NOT NULL
                );

                CREATE TABLE IF NOT EXISTS escalations (
                    id           INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id      TEXT NOT NULL,
                    user_chat_id TEXT NOT NULL,
                    user_name    TEXT,
                    username     TEXT,
                    question     TEXT NOT NULL,
                    status       TEXT NOT NULL DEFAULT 'open',
                    operator_id  TEXT,
                    operator_msg_mid TEXT,
                    created_at   REAL NOT NULL
                );

                CREATE TABLE IF NOT EXISTS operator_state (
                    operator_id   TEXT PRIMARY KEY,
                    escalation_id INTEGER NOT NULL,
                    created_at    REAL NOT NULL
                );

                CREATE TABLE IF NOT EXISTS appointments (
                    id           INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id      TEXT NOT NULL,
                    building_id  TEXT,
                    building_name TEXT,
                    time         TEXT,
                    created_at   REAL NOT NULL
                );

                -- Состояние пошагового опроса «Меры поддержки» ДО выбора меры
                -- (кем приходится участнику СВО, регион, телефон). Одна активная
                -- анкета на пользователя; data — JSON. Когда мера выбрана, дальше
                -- работает состояние сбора документов в applications.flow.
                CREATE TABLE IF NOT EXISTS bot_flows (
                    user_id     TEXT PRIMARY KEY,
                    stage       TEXT NOT NULL,
                    data        TEXT NOT NULL DEFAULT '{}',
                    updated_at  REAL NOT NULL
                );

                CREATE TABLE IF NOT EXISTS applications (
                    id            INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id       TEXT NOT NULL,
                    user_chat_id  TEXT,
                    user_name     TEXT,
                    username      TEXT,
                    measure_key   TEXT,
                    measure_title TEXT,
                    status        TEXT NOT NULL DEFAULT 'proposed',
                    data          TEXT,
                    decided_by    TEXT,
                    created_at    REAL NOT NULL,
                    updated_at    REAL NOT NULL,
                    decided_at    REAL
                );

                -- Меры поддержки, заводимые администратором в веб-кабинете.
                -- data — JSON: {documents:[{title,sort_order}], placeholders:[{key,label}],
                -- llm_hint, category}. template_path — путь к загруженному .docx-шаблону.
                CREATE TABLE IF NOT EXISTS support_measures (
                    id            INTEGER PRIMARY KEY AUTOINCREMENT,
                    title         TEXT NOT NULL,
                    description   TEXT,
                    data          TEXT,
                    template_path TEXT,
                    active        INTEGER NOT NULL DEFAULT 1,
                    created_at    REAL NOT NULL,
                    updated_at    REAL NOT NULL
                );

                -- Персональные карточки УСВО, загруженные из веб-кабинета (Excel).
                -- data — JSON со всеми полями карточки, сырой «Историей взаимодействия»
                -- и её нормализованным представлением (события с иконкой/статусом/стилем).
                CREATE TABLE IF NOT EXISTS usvo_cards (
                    id          INTEGER PRIMARY KEY AUTOINCREMENT,
                    name        TEXT,
                    status      TEXT,
                    phone       TEXT,
                    address     TEXT,
                    birth_date  TEXT,
                    call_date   TEXT,
                    awards      TEXT,
                    data        TEXT,
                    source      TEXT NOT NULL DEFAULT 'uploaded',
                    batch       TEXT,
                    created_at  REAL NOT NULL,
                    updated_at  REAL
                );

                -- Диалоги раздела «Чат с ИИ». user_id — стабильный sub из
                -- авторизованной веб-сессии; он обеспечивает изоляцию пользователей.
                CREATE TABLE IF NOT EXISTS ai_chats (
                    id          INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id     TEXT NOT NULL,
                    title       TEXT NOT NULL DEFAULT 'Новый чат',
                    answer_type TEXT NOT NULL DEFAULT 'text',
                    pinned      INTEGER NOT NULL DEFAULT 0,
                    created_at  REAL NOT NULL,
                    updated_at  REAL NOT NULL
                );

                CREATE TABLE IF NOT EXISTS ai_chat_messages (
                    id          INTEGER PRIMARY KEY AUTOINCREMENT,
                    chat_id     INTEGER NOT NULL,
                    role        TEXT NOT NULL,
                    content     TEXT NOT NULL,
                    metadata    TEXT NOT NULL DEFAULT '{}',
                    created_at  REAL NOT NULL,
                    FOREIGN KEY(chat_id) REFERENCES ai_chats(id) ON DELETE CASCADE
                );

                -- Состояние синхронизации карточек УСВО с отдельной базой знаний.
                CREATE TABLE IF NOT EXISTS ai_sync_state (
                    key         TEXT PRIMARY KEY,
                    value       TEXT,
                    updated_at  REAL NOT NULL
                );

                -- Хронология по обращению гражданина (задание «улучшить логирование»):
                -- события смены статуса, ответы оператора, отправленные уведомления.
                -- Не перезаписывается новыми обращениями — append-only по escalation_id.
                CREATE TABLE IF NOT EXISTS escalation_events (
                    id            INTEGER PRIMARY KEY AUTOINCREMENT,
                    escalation_id INTEGER NOT NULL,
                    kind          TEXT NOT NULL,
                    detail        TEXT,
                    created_at    REAL NOT NULL
                );

                -- Аудит-лог действий оператора (госсектор: нужен надёжный след).
                -- Пишется best-effort — сбой записи аудита не должен ломать действие.
                CREATE TABLE IF NOT EXISTS audit_log (
                    id         INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_sub   TEXT,
                    user_name  TEXT,
                    action     TEXT NOT NULL,
                    entity     TEXT,
                    entity_id  TEXT,
                    details    TEXT,
                    at         REAL NOT NULL
                );

                -- Редактируемые администратором настройки кабинета (регламент SLA
                -- и т.п.). Простое key/value: значение хранится строкой, слой выше
                -- сам приводит тип и подставляет дефолт из конфига.
                CREATE TABLE IF NOT EXISTS app_settings (
                    key        TEXT PRIMARY KEY,
                    value      TEXT,
                    updated_at REAL NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_ai_chats_user_updated
                    ON ai_chats(user_id, updated_at DESC);
                CREATE INDEX IF NOT EXISTS idx_ai_messages_chat_created
                    ON ai_chat_messages(chat_id, created_at ASC, id ASC);
                CREATE INDEX IF NOT EXISTS idx_escalation_events_esc
                    ON escalation_events(escalation_id, id ASC);
                CREATE INDEX IF NOT EXISTS idx_audit_log_at
                    ON audit_log(at DESC, id DESC);
                """
            )
            # Миграции для существующих БД (добавленные позже столбцы).
            self._ensure_column(c, "users", "questions_since_offer", "INTEGER NOT NULL DEFAULT 0")
            self._ensure_column(c, "escalations", "operator_msg_mid", "TEXT")
            # Поля, которыми пользуется веб-кабинет оператора.
            self._ensure_column(c, "escalations", "assignee", "TEXT")
            self._ensure_column(c, "escalations", "answer", "TEXT")
            self._ensure_column(c, "escalations", "answered_at", "REAL")
            self._ensure_column(c, "escalations", "topic", "TEXT")
            # updated_at у загруженных карточек УСВО — нужен, чтобы кэш загрузчика
            # перечитывал БД после редактирования (изменение «на месте» не меняет
            # ни число строк, ни created_at).
            self._ensure_column(c, "usvo_cards", "updated_at", "REAL")
            # Сценарий «Меры поддержки» в боте: к заявлению привязывается выбранная
            # мера и состояние пошагового сбора документов (flow — JSON-строка).
            self._ensure_column(c, "applications", "support_measure_id", "INTEGER")
            self._ensure_column(c, "applications", "flow", "TEXT")
            # Подписка гражданина на уведомления (экран финала оформления меры).
            self._ensure_column(c, "users", "subscribed", "INTEGER NOT NULL DEFAULT 0")
            # Профиль гражданина, собираемый вступительной анкетой один раз: родство с
            # участником СВО, регистрация в МО и город. Телефон уже в users.phone.
            # Переиспользуется при повторных обращениях, чтобы не переспрашивать.
            self._ensure_column(c, "users", "relation", "TEXT")
            self._ensure_column(c, "users", "region_ok", "INTEGER")
            self._ensure_column(c, "users", "locality", "TEXT")
            # SLA-таймеры обращений: телефон гражданина (для связи с карточкой УСВО и
            # истории) и отметка о повторном уведомлении операторов о просрочке.
            self._ensure_column(c, "escalations", "phone", "TEXT")
            self._ensure_column(c, "escalations", "sla_notified_at", "REAL")
            # Закреплённые диалоги «ИИ ассистента» — всегда в начале списка.
            self._ensure_column(c, "ai_chats", "pinned", "INTEGER NOT NULL DEFAULT 0")

    @staticmethod
    def _ensure_column(conn, table: str, column: str, ddl: str) -> None:
        cols = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}
        if column not in cols:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}")

    # ---- users / questions -------------------------------------------------

    def ensure_user(self, user_id: str, chat_id: str, name: str = "", username: str = "") -> None:
        with self._conn() as c:
            c.execute(
                """
                INSERT INTO users (user_id, chat_id, name, username)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(user_id) DO UPDATE SET
                    chat_id = excluded.chat_id,
                    name = COALESCE(NULLIF(excluded.name, ''), users.name),
                    username = COALESCE(NULLIF(excluded.username, ''), users.username)
                """,
                (user_id, chat_id, name, username),
            )

    def get_user(self, user_id: str) -> sqlite3.Row | None:
        with self._conn() as c:
            return c.execute("SELECT * FROM users WHERE user_id = ?", (user_id,)).fetchone()

    def list_users(self, subscribed_only: bool = False, limit: int = 100000) -> list[sqlite3.Row]:
        """Пользователи MAX для рассылок.

        subscribed_only=True — только подписанные на уведомления (users.subscribed=1).
        Иначе — все, кто когда-либо писал боту (есть строка в users)."""
        with self._conn() as c:
            self._ensure_column(c, "users", "subscribed", "INTEGER NOT NULL DEFAULT 0")
            if subscribed_only:
                return c.execute(
                    "SELECT * FROM users WHERE subscribed = 1 ORDER BY user_id LIMIT ?",
                    (limit,),
                ).fetchall()
            return c.execute(
                "SELECT * FROM users ORDER BY user_id LIMIT ?", (limit,)
            ).fetchall()

    def add_question(self, user_id: str, text: str) -> int:
        """Сохраняет вопрос и инкрементит счётчики.

        Возвращает questions_since_offer — число вопросов с момента прошлого предложения
        записи (сбрасывается в 0 после показа оффера, см. reset_since_offer).
        """
        with self._conn() as c:
            c.execute(
                "INSERT INTO questions (user_id, text, created_at) VALUES (?, ?, ?)",
                (user_id, text, time.time()),
            )
            c.execute(
                "UPDATE users SET question_count = question_count + 1, "
                "questions_since_offer = questions_since_offer + 1 WHERE user_id = ?",
                (user_id,),
            )
            row = c.execute(
                "SELECT questions_since_offer FROM users WHERE user_id = ?", (user_id,)
            ).fetchone()
            return int(row["questions_since_offer"]) if row else 1

    def reset_since_offer(self, user_id: str) -> None:
        """Сбрасывает счётчик вопросов до следующего предложения записи."""
        with self._conn() as c:
            c.execute(
                "UPDATE users SET questions_since_offer = 0 WHERE user_id = ?", (user_id,)
            )

    def last_questions(self, user_id: str, n: int = 3) -> list[str]:
        with self._conn() as c:
            rows = c.execute(
                "SELECT text FROM questions WHERE user_id = ? ORDER BY id DESC LIMIT ?",
                (user_id, n),
            ).fetchall()
        return [r["text"] for r in reversed(rows)]

    def set_user_phone(self, user_id: str, phone: str) -> None:
        """Сохраняет контактный телефон гражданина (шаг «поделиться номером»)."""
        with self._conn() as c:
            c.execute(
                "UPDATE users SET phone = ? WHERE user_id = ?", (phone, str(user_id))
            )

    def set_user_subscribed(self, user_id: str, subscribed: bool) -> None:
        """Отмечает подписку гражданина на уведомления (финал оформления меры)."""
        with self._conn() as c:
            self._ensure_column(c, "users", "subscribed", "INTEGER NOT NULL DEFAULT 0")
            c.execute(
                "UPDATE users SET subscribed = ? WHERE user_id = ?",
                (1 if subscribed else 0, str(user_id)),
            )

    def set_user_profile(
        self,
        user_id: str,
        *,
        relation: str | None = None,
        region_ok: bool | None = None,
        locality: str | None = None,
        phone: str | None = None,
    ) -> None:
        """Сохраняет поля вступительной анкеты (родство/регистрация/город/телефон).

        Обновляются только переданные (не None) поля — чтобы дозапись одного поля
        не затирала уже собранные. Используется для переиспользования профиля при
        повторных обращениях без повторного опроса.
        """
        sets: list[str] = []
        vals: list[object] = []
        if relation is not None:
            sets.append("relation = ?"); vals.append(relation)
        if region_ok is not None:
            sets.append("region_ok = ?"); vals.append(1 if region_ok else 0)
        if locality is not None:
            sets.append("locality = ?"); vals.append(locality)
        if phone is not None:
            sets.append("phone = ?"); vals.append(phone)
        if not sets:
            return
        vals.append(str(user_id))
        with self._conn() as c:
            self._ensure_column(c, "users", "relation", "TEXT")
            self._ensure_column(c, "users", "region_ok", "INTEGER")
            self._ensure_column(c, "users", "locality", "TEXT")
            c.execute(f"UPDATE users SET {', '.join(sets)} WHERE user_id = ?", vals)

    def clear_user_profile(self, user_id: str) -> None:
        """Сбрасывает профиль анкеты (родство/регистрация/город/телефон).

        Вызывается по команде /start — чтобы гражданин мог заново пройти
        вступительную анкету и обновить сохранённые данные.
        """
        with self._conn() as c:
            self._ensure_column(c, "users", "relation", "TEXT")
            self._ensure_column(c, "users", "region_ok", "INTEGER")
            self._ensure_column(c, "users", "locality", "TEXT")
            c.execute(
                "UPDATE users SET relation = NULL, region_ok = NULL, "
                "locality = NULL, phone = NULL WHERE user_id = ?",
                (str(user_id),),
            )

    # ---- анкета «Меры поддержки» (до выбора меры) --------------------------

    def set_bot_flow(self, user_id: str, stage: str, data: str = "{}") -> None:
        """Создаёт/обновляет состояние опроса «Меры поддержки» для пользователя."""
        with self._conn() as c:
            c.execute(
                """
                INSERT INTO bot_flows (user_id, stage, data, updated_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(user_id) DO UPDATE SET
                    stage = excluded.stage,
                    data = excluded.data,
                    updated_at = excluded.updated_at
                """,
                (str(user_id), stage, data, time.time()),
            )

    def get_bot_flow(self, user_id: str) -> sqlite3.Row | None:
        with self._conn() as c:
            return c.execute(
                "SELECT * FROM bot_flows WHERE user_id = ?", (str(user_id),)
            ).fetchone()

    def clear_bot_flow(self, user_id: str) -> None:
        with self._conn() as c:
            c.execute("DELETE FROM bot_flows WHERE user_id = ?", (str(user_id),))

    # ---- escalations -------------------------------------------------------

    def create_escalation(
        self, user_id: str, user_chat_id: str, user_name: str, username: str, question: str,
        phone: str = "",
    ) -> int:
        now = time.time()
        with self._conn() as c:
            self._ensure_column(c, "escalations", "phone", "TEXT")
            cur = c.execute(
                """
                INSERT INTO escalations
                    (user_id, user_chat_id, user_name, username, question, status,
                     phone, created_at)
                VALUES (?, ?, ?, ?, ?, 'open', ?, ?)
                """,
                (user_id, user_chat_id, user_name, username, question, phone, now),
            )
            esc_id = int(cur.lastrowid)
            # Стартовое событие хронологии обращения.
            c.execute(
                "INSERT INTO escalation_events (escalation_id, kind, detail, created_at) "
                "VALUES (?, 'created', ?, ?)",
                (esc_id, "Обращение создано", now),
            )
            return esc_id

    def get_escalation(self, escalation_id: int) -> sqlite3.Row | None:
        with self._conn() as c:
            return c.execute(
                "SELECT * FROM escalations WHERE id = ?", (escalation_id,)
            ).fetchone()

    def set_escalation_mid(self, escalation_id: int, mid: str) -> None:
        """Запоминает id сообщения-эскалации в чате операторов (для ответа через reply)."""
        with self._conn() as c:
            c.execute(
                "UPDATE escalations SET operator_msg_mid = ? WHERE id = ?", (mid, escalation_id)
            )

    def get_open_escalation_by_mid(self, mid: str) -> sqlite3.Row | None:
        with self._conn() as c:
            return c.execute(
                "SELECT * FROM escalations WHERE operator_msg_mid = ? AND status = 'open' "
                "ORDER BY id DESC LIMIT 1",
                (mid,),
            ).fetchone()

    def close_escalation(self, escalation_id: int, operator_id: str) -> None:
        with self._conn() as c:
            c.execute(
                "UPDATE escalations SET status = 'answered', operator_id = ? WHERE id = ?",
                (operator_id, escalation_id),
            )

    # ---- operator reply state ---------------------------------------------

    def set_operator_state(self, operator_id: str, escalation_id: int) -> None:
        with self._conn() as c:
            c.execute(
                """
                INSERT INTO operator_state (operator_id, escalation_id, created_at)
                VALUES (?, ?, ?)
                ON CONFLICT(operator_id) DO UPDATE SET
                    escalation_id = excluded.escalation_id,
                    created_at = excluded.created_at
                """,
                (operator_id, escalation_id, time.time()),
            )

    def pop_operator_state(self, operator_id: str) -> int | None:
        """Возвращает escalation_id, на который отвечает оператор, и снимает состояние."""
        with self._conn() as c:
            row = c.execute(
                "SELECT escalation_id FROM operator_state WHERE operator_id = ?",
                (operator_id,),
            ).fetchone()
            if not row:
                return None
            c.execute("DELETE FROM operator_state WHERE operator_id = ?", (operator_id,))
            return int(row["escalation_id"])

    # ---- appointments ------------------------------------------------------

    def create_appointment(
        self, user_id: str, building_id: str, building_name: str, time_str: str
    ) -> int:
        with self._conn() as c:
            cur = c.execute(
                """
                INSERT INTO appointments
                    (user_id, building_id, building_name, time, created_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (user_id, building_id, building_name, time_str, time.time()),
            )
            return int(cur.lastrowid)

    # ---- заявления (мера поддержки по фото) --------------------------------

    def create_application(
        self, user_id: str, user_chat_id: str, user_name: str, username: str,
        measure_key: str, measure_title: str, data: str, status: str = "proposed",
    ) -> int:
        now = time.time()
        with self._conn() as c:
            cur = c.execute(
                """
                INSERT INTO applications
                    (user_id, user_chat_id, user_name, username, measure_key,
                     measure_title, status, data, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (user_id, user_chat_id, user_name, username, measure_key,
                 measure_title, status, data, now, now),
            )
            return int(cur.lastrowid)

    def get_application(self, application_id: int) -> sqlite3.Row | None:
        with self._conn() as c:
            return c.execute(
                "SELECT * FROM applications WHERE id = ?", (application_id,)
            ).fetchone()

    def get_pending_application(self, user_id: str) -> sqlite3.Row | None:
        """Последнее заявление пользователя, ожидающее подтверждения подачи.

        Нужно для сценария «пользователь вместо кнопки прислал свой файл» — тогда
        присланный файл считаем самим заявлением и подаём именно его.
        """
        with self._conn() as c:
            return c.execute(
                "SELECT * FROM applications WHERE user_id = ? AND status = 'awaiting_confirm' "
                "ORDER BY id DESC LIMIT 1",
                (str(user_id),),
            ).fetchone()

    def update_application_data(self, application_id: int, data: str) -> None:
        with self._conn() as c:
            c.execute(
                "UPDATE applications SET data = ?, updated_at = ? WHERE id = ?",
                (data, time.time(), application_id),
            )

    def set_application_status(
        self, application_id: int, status: str, decided_by: str | None = None
    ) -> None:
        now = time.time()
        with self._conn() as c:
            if decided_by is not None:
                c.execute(
                    "UPDATE applications SET status = ?, decided_by = ?, decided_at = ?, "
                    "updated_at = ? WHERE id = ?",
                    (status, decided_by, now, now, application_id),
                )
            else:
                c.execute(
                    "UPDATE applications SET status = ?, updated_at = ? WHERE id = ?",
                    (status, now, application_id),
                )

    def list_applications(self, limit: int = 500) -> list[sqlite3.Row]:
        with self._conn() as c:
            return c.execute(
                "SELECT * FROM applications ORDER BY id DESC LIMIT ?", (limit,)
            ).fetchall()

    def delete_application(self, application_id: int) -> bool:
        with self._conn() as c:
            cur = c.execute("DELETE FROM applications WHERE id = ?", (application_id,))
            return cur.rowcount > 0

    # ---- состояние пошагового сбора документов («Меры поддержки») ----------

    # Промежуточные статусы заявки во время сбора документов по выбранной мере.
    FLOW_STATUSES = (
        "started", "waiting_document", "documents_uploaded", "extracting_data",
        "waiting_missing_fields", "ready_for_confirmation",
    )

    def create_measure_application(
        self, user_id: str, user_chat_id: str, user_name: str, username: str,
        measure_id: int, measure_title: str, flow: str, data: str = "{}",
        status: str = "started",
    ) -> int:
        now = time.time()
        with self._conn() as c:
            cur = c.execute(
                """
                INSERT INTO applications
                    (user_id, user_chat_id, user_name, username, measure_key,
                     measure_title, status, data, support_measure_id, flow,
                     created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (user_id, user_chat_id, user_name, username, f"measure-{measure_id}",
                 measure_title, status, data, measure_id, flow, now, now),
            )
            return int(cur.lastrowid)

    def update_application_flow(self, application_id: int, flow: str) -> None:
        with self._conn() as c:
            c.execute(
                "UPDATE applications SET flow = ?, updated_at = ? WHERE id = ?",
                (flow, time.time(), application_id),
            )

    def get_active_flow(self, user_id: str) -> sqlite3.Row | None:
        """Последнее заявление пользователя в незавершённом статусе сбора документов.

        Нужно, чтобы входящие фото/текст направлять в активный сценарий «Меры
        поддержки», а не в старый авто-ЖКУ по фото.
        """
        placeholders = ", ".join("?" for _ in self.FLOW_STATUSES)
        with self._conn() as c:
            return c.execute(
                f"SELECT * FROM applications WHERE user_id = ? AND flow IS NOT NULL "
                f"AND status IN ({placeholders}) ORDER BY id DESC LIMIT 1",
                (str(user_id), *self.FLOW_STATUSES),
            ).fetchone()

    # ---- меры поддержки (CRUD из веб-кабинета) -----------------------------

    def add_support_measure(self, measure: dict) -> int:
        now = time.time()
        with self._conn() as c:
            cur = c.execute(
                """
                INSERT INTO support_measures
                    (title, description, data, template_path, active, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    measure.get("title", ""), measure.get("description", ""),
                    measure.get("data", "{}"), measure.get("template_path", ""),
                    1 if measure.get("active", True) else 0, now, now,
                ),
            )
            return int(cur.lastrowid)

    def get_support_measure(self, measure_id: int) -> sqlite3.Row | None:
        with self._conn() as c:
            return c.execute(
                "SELECT * FROM support_measures WHERE id = ?", (measure_id,)
            ).fetchone()

    def list_support_measures(self, active_only: bool = False) -> list[sqlite3.Row]:
        with self._conn() as c:
            if active_only:
                return c.execute(
                    "SELECT * FROM support_measures WHERE active = 1 ORDER BY id ASC"
                ).fetchall()
            return c.execute(
                "SELECT * FROM support_measures ORDER BY id ASC"
            ).fetchall()

    def update_support_measure(self, measure_id: int, measure: dict) -> bool:
        with self._conn() as c:
            cur = c.execute(
                """
                UPDATE support_measures SET
                    title = ?, description = ?, data = ?, template_path = ?,
                    active = ?, updated_at = ?
                WHERE id = ?
                """,
                (
                    measure.get("title", ""), measure.get("description", ""),
                    measure.get("data", "{}"), measure.get("template_path", ""),
                    1 if measure.get("active", True) else 0, time.time(), measure_id,
                ),
            )
            return cur.rowcount > 0

    def set_support_measure_active(self, measure_id: int, active: bool) -> bool:
        with self._conn() as c:
            cur = c.execute(
                "UPDATE support_measures SET active = ?, updated_at = ? WHERE id = ?",
                (1 if active else 0, time.time(), measure_id),
            )
            return cur.rowcount > 0

    def set_support_measure_template(self, measure_id: int, template_path: str) -> bool:
        with self._conn() as c:
            cur = c.execute(
                "UPDATE support_measures SET template_path = ?, updated_at = ? WHERE id = ?",
                (template_path, time.time(), measure_id),
            )
            return cur.rowcount > 0

    def delete_support_measure(self, measure_id: int) -> bool:
        with self._conn() as c:
            cur = c.execute("DELETE FROM support_measures WHERE id = ?", (measure_id,))
            return cur.rowcount > 0

    # ---- read-only выборки для веб-кабинета --------------------------------

    def list_escalations(self, limit: int = 500) -> list[sqlite3.Row]:
        with self._conn() as c:
            return c.execute(
                "SELECT * FROM escalations ORDER BY id DESC LIMIT ?", (limit,)
            ).fetchall()

    def count_escalations(self) -> int:
        with self._conn() as c:
            return int(c.execute("SELECT COUNT(*) AS n FROM escalations").fetchone()["n"])

    def list_questions(self, limit: int = 2000) -> list[sqlite3.Row]:
        with self._conn() as c:
            return c.execute(
                "SELECT * FROM questions ORDER BY id DESC LIMIT ?", (limit,)
            ).fetchall()

    def list_appointments(self, limit: int = 1000) -> list[sqlite3.Row]:
        with self._conn() as c:
            return c.execute(
                "SELECT * FROM appointments ORDER BY id DESC LIMIT ?", (limit,)
            ).fetchall()

    def set_escalation_assignee(self, escalation_id: int, assignee: str) -> None:
        with self._conn() as c:
            self._ensure_column(c, "escalations", "assignee", "TEXT")
            c.execute(
                "UPDATE escalations SET assignee = ? WHERE id = ?", (assignee, escalation_id)
            )

    def set_escalation_answer(self, escalation_id: int, answer: str, assignee: str = "") -> None:
        """Сохраняет ответ оператора, данный из веб-кабинета, и закрывает обращение."""
        now = time.time()
        with self._conn() as c:
            self._ensure_column(c, "escalations", "answer", "TEXT")
            self._ensure_column(c, "escalations", "answered_at", "REAL")
            self._ensure_column(c, "escalations", "assignee", "TEXT")
            if assignee:
                c.execute(
                    "UPDATE escalations SET answer = ?, answered_at = ?, status = 'answered', "
                    "assignee = ? WHERE id = ?",
                    (answer, now, assignee, escalation_id),
                )
            else:
                c.execute(
                    "UPDATE escalations SET answer = ?, answered_at = ?, status = 'answered' "
                    "WHERE id = ?",
                    (answer, now, escalation_id),
                )
            detail = "Ответ оператора" + (f" ({assignee})" if assignee else "")
            c.execute(
                "INSERT INTO escalation_events (escalation_id, kind, detail, created_at) "
                "VALUES (?, 'answered', ?, ?)",
                (escalation_id, detail, now),
            )

    # ---- хронология и SLA обращений ---------------------------------------

    def add_escalation_event(self, escalation_id: int, kind: str, detail: str = "") -> None:
        """Добавляет событие в хронологию обращения (append-only).

        Используется для фиксации смены статуса и отправленных гражданину
        уведомлений. Не бросает при отсутствии обращения."""
        with self._conn() as c:
            c.execute(
                "INSERT INTO escalation_events (escalation_id, kind, detail, created_at) "
                "VALUES (?, ?, ?, ?)",
                (escalation_id, kind, detail, time.time()),
            )

    def list_escalation_events(self, escalation_id: int) -> list[sqlite3.Row]:
        with self._conn() as c:
            return c.execute(
                "SELECT * FROM escalation_events WHERE escalation_id = ? ORDER BY id ASC",
                (escalation_id,),
            ).fetchall()

    def list_open_escalations(self, limit: int = 1000) -> list[sqlite3.Row]:
        """Необработанные обращения (status='open') — для SLA-напоминаний."""
        with self._conn() as c:
            return c.execute(
                "SELECT * FROM escalations WHERE status = 'open' ORDER BY id ASC LIMIT ?",
                (limit,),
            ).fetchall()

    def set_escalation_sla_notified(self, escalation_id: int, ts: float | None = None) -> None:
        """Отмечает, что операторам уже отправлено напоминание о просрочке."""
        with self._conn() as c:
            self._ensure_column(c, "escalations", "sla_notified_at", "REAL")
            c.execute(
                "UPDATE escalations SET sla_notified_at = ? WHERE id = ?",
                (ts if ts is not None else time.time(), escalation_id),
            )

    def delete_escalation(self, escalation_id: int) -> bool:
        """Удаляет обращение оператора из веб-кабинета (вместе с его хронологией)."""
        with self._conn() as c:
            c.execute("DELETE FROM operator_state WHERE escalation_id = ?", (escalation_id,))
            c.execute("DELETE FROM escalation_events WHERE escalation_id = ?", (escalation_id,))
            cur = c.execute("DELETE FROM escalations WHERE id = ?", (escalation_id,))
            return cur.rowcount > 0

    # ---- история обращений по пользователю MAX -----------------------------

    def list_escalations_by_user(self, user_id: str, limit: int = 100) -> list[sqlite3.Row]:
        """Все обращения одного гражданина (по его id в MAX) — для истории."""
        with self._conn() as c:
            return c.execute(
                "SELECT * FROM escalations WHERE user_id = ? ORDER BY id DESC LIMIT ?",
                (str(user_id), limit),
            ).fetchall()

    # ---- веб-чат с ИИ ------------------------------------------------------

    def create_ai_chat(
        self, user_id: str, title: str = "Новый чат", answer_type: str = "text"
    ) -> int:
        now = time.time()
        with self._conn() as c:
            cur = c.execute(
                """
                INSERT INTO ai_chats (user_id, title, answer_type, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (str(user_id), title, answer_type, now, now),
            )
            return int(cur.lastrowid)

    def list_ai_chats(self, user_id: str, limit: int = 200) -> list[sqlite3.Row]:
        with self._conn() as c:
            return c.execute(
                """
                SELECT c.*,
                       (SELECT COUNT(*) FROM ai_chat_messages m WHERE m.chat_id = c.id)
                           AS message_count
                FROM ai_chats c
                WHERE c.user_id = ?
                ORDER BY COALESCE(c.pinned, 0) DESC, c.updated_at DESC, c.id DESC
                LIMIT ?
                """,
                (str(user_id), limit),
            ).fetchall()

    def get_ai_chat(self, chat_id: int, user_id: str) -> sqlite3.Row | None:
        with self._conn() as c:
            return c.execute(
                "SELECT * FROM ai_chats WHERE id = ? AND user_id = ?",
                (chat_id, str(user_id)),
            ).fetchone()

    def update_ai_chat(
        self,
        chat_id: int,
        user_id: str,
        *,
        title: str | None = None,
        answer_type: str | None = None,
        pinned: bool | None = None,
    ) -> bool:
        sets: list[str] = []
        values: list = []
        if title is not None:
            sets.append("title = ?")
            values.append(title)
        if answer_type is not None:
            sets.append("answer_type = ?")
            values.append(answer_type)
        if pinned is not None:
            sets.append("pinned = ?")
            values.append(1 if pinned else 0)
        if not sets:
            return self.get_ai_chat(chat_id, user_id) is not None
        # Закрепление НЕ поднимает чат в списке «последних»: updated_at трогаем
        # только при реальном изменении содержимого (заголовок / формат ответа).
        if title is not None or answer_type is not None:
            sets.append("updated_at = ?")
            values.append(time.time())
        values.extend([chat_id, str(user_id)])
        with self._conn() as c:
            cur = c.execute(
                f"UPDATE ai_chats SET {', '.join(sets)} WHERE id = ? AND user_id = ?",
                values,
            )
            return cur.rowcount > 0

    def delete_ai_chat(self, chat_id: int, user_id: str) -> bool:
        with self._conn() as c:
            cur = c.execute(
                "DELETE FROM ai_chats WHERE id = ? AND user_id = ?",
                (chat_id, str(user_id)),
            )
            return cur.rowcount > 0

    def add_ai_chat_message(
        self,
        chat_id: int,
        user_id: str,
        role: str,
        content: str,
        metadata: str = "{}",
    ) -> int | None:
        now = time.time()
        with self._conn() as c:
            owner = c.execute(
                "SELECT 1 FROM ai_chats WHERE id = ? AND user_id = ?",
                (chat_id, str(user_id)),
            ).fetchone()
            if owner is None:
                return None
            cur = c.execute(
                """
                INSERT INTO ai_chat_messages (chat_id, role, content, metadata, created_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (chat_id, role, content, metadata or "{}", now),
            )
            c.execute("UPDATE ai_chats SET updated_at = ? WHERE id = ?", (now, chat_id))
            return int(cur.lastrowid)

    def list_ai_chat_messages(
        self, chat_id: int, user_id: str, limit: int = 1000
    ) -> list[sqlite3.Row] | None:
        with self._conn() as c:
            owner = c.execute(
                "SELECT 1 FROM ai_chats WHERE id = ? AND user_id = ?",
                (chat_id, str(user_id)),
            ).fetchone()
            if owner is None:
                return None
            return c.execute(
                """
                SELECT * FROM ai_chat_messages
                WHERE chat_id = ?
                ORDER BY created_at ASC, id ASC
                LIMIT ?
                """,
                (chat_id, limit),
            ).fetchall()

    def get_ai_chat_message(
        self, message_id: int, chat_id: int, user_id: str
    ) -> sqlite3.Row | None:
        with self._conn() as c:
            return c.execute(
                """
                SELECT m.* FROM ai_chat_messages m
                JOIN ai_chats c ON c.id = m.chat_id
                WHERE m.id = ? AND m.chat_id = ? AND c.user_id = ?
                """,
                (message_id, chat_id, str(user_id)),
            ).fetchone()

    def get_ai_sync_state(self, key: str) -> str | None:
        with self._conn() as c:
            row = c.execute("SELECT value FROM ai_sync_state WHERE key = ?", (key,)).fetchone()
            return str(row["value"]) if row and row["value"] is not None else None

    def set_ai_sync_state(self, key: str, value: str) -> None:
        with self._conn() as c:
            c.execute(
                """
                INSERT INTO ai_sync_state (key, value, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT(key) DO UPDATE SET value = excluded.value,
                                               updated_at = excluded.updated_at
                """,
                (key, value, time.time()),
            )

    # ---- карточки УСВО, загруженные из веб-кабинета ------------------------

    def add_usvo_card(self, card: dict, batch: str = "") -> int:
        """Сохраняет одну загруженную карточку. `card` — словарь с ключами
        name/status/phone/address/birth_date/call_date/awards и `data` (JSON-строка)."""
        now = time.time()
        with self._conn() as c:
            cur = c.execute(
                """
                INSERT INTO usvo_cards
                    (name, status, phone, address, birth_date, call_date, awards,
                     data, source, batch, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'uploaded', ?, ?, ?)
                """,
                (
                    card.get("name", ""), card.get("status", ""), card.get("phone", ""),
                    card.get("address", ""), card.get("birth_date", ""),
                    card.get("call_date", ""), card.get("awards", ""),
                    card.get("data", "{}"), batch, now, now,
                ),
            )
            return int(cur.lastrowid)

    def update_usvo_card(self, card_id: int, card: dict) -> bool:
        """Обновляет поля и `data` загруженной карточки УСВО (редактирование из кабинета)."""
        with self._conn() as c:
            cur = c.execute(
                """
                UPDATE usvo_cards SET
                    name = ?, status = ?, phone = ?, address = ?, birth_date = ?,
                    call_date = ?, awards = ?, data = ?, updated_at = ?
                WHERE id = ?
                """,
                (
                    card.get("name", ""), card.get("status", ""), card.get("phone", ""),
                    card.get("address", ""), card.get("birth_date", ""),
                    card.get("call_date", ""), card.get("awards", ""),
                    card.get("data", "{}"), time.time(), card_id,
                ),
            )
            return cur.rowcount > 0

    def list_usvo_cards(self, limit: int = 5000) -> list[sqlite3.Row]:
        with self._conn() as c:
            return c.execute(
                "SELECT * FROM usvo_cards ORDER BY id ASC LIMIT ?", (limit,)
            ).fetchall()

    def get_usvo_card(self, card_id: int) -> sqlite3.Row | None:
        with self._conn() as c:
            return c.execute(
                "SELECT * FROM usvo_cards WHERE id = ?", (card_id,)
            ).fetchone()

    def count_usvo_cards(self) -> int:
        with self._conn() as c:
            return int(c.execute("SELECT COUNT(*) AS n FROM usvo_cards").fetchone()["n"])

    def delete_usvo_card(self, card_id: int) -> bool:
        with self._conn() as c:
            cur = c.execute("DELETE FROM usvo_cards WHERE id = ?", (card_id,))
            return cur.rowcount > 0

    def clear_usvo_cards(self) -> int:
        with self._conn() as c:
            cur = c.execute("DELETE FROM usvo_cards")
            return cur.rowcount

    # ---- аудит-лог действий оператора --------------------------------------

    def add_audit_log(
        self, action: str, *, user_sub: str = "", user_name: str = "",
        entity: str = "", entity_id: str = "", details: str = "",
    ) -> int:
        """Пишет запись аудита. Вызывающий оборачивает вызов в try/except —
        сбой аудита не должен ломать основное действие."""
        with self._conn() as c:
            cur = c.execute(
                """
                INSERT INTO audit_log
                    (user_sub, user_name, action, entity, entity_id, details, at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (user_sub, user_name, action, entity, str(entity_id), details, time.time()),
            )
            return int(cur.lastrowid)

    def list_audit_log(
        self, limit: int = 500, entity: str = "", action: str = ""
    ) -> list[sqlite3.Row]:
        clauses: list[str] = []
        params: list = []
        if entity:
            clauses.append("entity = ?"); params.append(entity)
        if action:
            clauses.append("action = ?"); params.append(action)
        where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
        params.append(limit)
        with self._conn() as c:
            return c.execute(
                f"SELECT * FROM audit_log{where} ORDER BY id DESC LIMIT ?", params
            ).fetchall()

    # ---- настройки кабинета (key/value) -----------------------------------

    def get_setting(self, key: str, default: str | None = None) -> str | None:
        """Значение настройки (строкой) или default, если не задана."""
        with self._conn() as c:
            row = c.execute(
                "SELECT value FROM app_settings WHERE key = ?", (key,)
            ).fetchone()
        return row["value"] if row is not None else default

    def set_setting(self, key: str, value: str) -> None:
        with self._conn() as c:
            c.execute(
                """
                INSERT INTO app_settings (key, value, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT(key) DO UPDATE SET
                    value = excluded.value,
                    updated_at = excluded.updated_at
                """,
                (key, str(value), time.time()),
            )
