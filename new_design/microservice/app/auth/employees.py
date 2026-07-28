"""Хранилище учётных записей сотрудников (роль «Сотрудник») в ОТДЕЛЬНОЙ БД."""
from __future__ import annotations

import hashlib
import hmac
import os
import secrets
import sqlite3
import time
from contextlib import contextmanager

_PBKDF2_ITERS = 200_000


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, _PBKDF2_ITERS)
    return f"pbkdf2${_PBKDF2_ITERS}${salt.hex()}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        algo, iters, salt_hex, hash_hex = (stored or "").split("$", 3)
        if algo != "pbkdf2":
            return False
        digest = hashlib.pbkdf2_hmac(
            "sha256", password.encode("utf-8"), bytes.fromhex(salt_hex), int(iters)
        )
        return hmac.compare_digest(digest.hex(), hash_hex)
    except Exception:  # noqa: BLE001
        return False


def compose_name(last_name: str = "", first_name: str = "", middle_name: str = "") -> str:
    return " ".join(p for p in [last_name.strip(), first_name.strip(), middle_name.strip()] if p)


def split_name(name: str) -> tuple[str, str, str]:
    parts = [p for p in (name or "").strip().split() if p]
    if len(parts) >= 3:
        return parts[0], parts[1], " ".join(parts[2:])
    if len(parts) == 2:
        return parts[0], parts[1], ""
    if len(parts) == 1:
        return "", parts[0], ""
    return "", "", ""


class EmployeeStore:
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
            yield conn
            conn.commit()
        finally:
            conn.close()

    def _init_db(self) -> None:
        with self._conn() as c:
            c.execute(
                """
                CREATE TABLE IF NOT EXISTS employees (
                    id            INTEGER PRIMARY KEY AUTOINCREMENT,
                    name          TEXT NOT NULL,
                    login         TEXT NOT NULL UNIQUE COLLATE NOCASE,
                    password_hash TEXT NOT NULL,
                    position      TEXT NOT NULL DEFAULT '',
                    phone         TEXT NOT NULL DEFAULT '',
                    active        INTEGER NOT NULL DEFAULT 1,
                    created_at    REAL NOT NULL,
                    updated_at    REAL NOT NULL
                );
                """
            )
            self._ensure_column(c, "employees", "last_name", "TEXT NOT NULL DEFAULT ''")
            self._ensure_column(c, "employees", "first_name", "TEXT NOT NULL DEFAULT ''")
            self._ensure_column(c, "employees", "middle_name", "TEXT NOT NULL DEFAULT ''")
            self._ensure_column(c, "employees", "birth_date", "TEXT NOT NULL DEFAULT ''")
            c.execute(
                """
                CREATE TABLE IF NOT EXISTS user_profiles (
                    sub         TEXT PRIMARY KEY,
                    last_name   TEXT NOT NULL DEFAULT '',
                    first_name  TEXT NOT NULL DEFAULT '',
                    middle_name TEXT NOT NULL DEFAULT '',
                    birth_date  TEXT NOT NULL DEFAULT '',
                    phone       TEXT NOT NULL DEFAULT '',
                    updated_at  REAL NOT NULL
                );
                """
            )
            self._backfill_fio(c)

    @staticmethod
    def _ensure_column(c: sqlite3.Connection, table: str, column: str, typedef: str) -> None:
        cols = {row[1] for row in c.execute(f"PRAGMA table_info({table})").fetchall()}
        if column not in cols:
            c.execute(f"ALTER TABLE {table} ADD COLUMN {column} {typedef}")

    @staticmethod
    def _backfill_fio(c: sqlite3.Connection) -> None:
        rows = c.execute(
            "SELECT id, name, last_name, first_name, middle_name FROM employees"
        ).fetchall()
        for row in rows:
            if (row["last_name"] or "").strip() or (row["first_name"] or "").strip():
                continue
            last_name, first_name, middle_name = split_name(row["name"] or "")
            if not (last_name or first_name or middle_name):
                continue
            c.execute(
                "UPDATE employees SET last_name=?, first_name=?, middle_name=? WHERE id=?",
                (last_name, first_name, middle_name, row["id"]),
            )

    # ---- чтение -------------------------------------------------------------

    @staticmethod
    def _fio_from_row(row: sqlite3.Row) -> tuple[str, str, str]:
        last_name = (row["last_name"] if "last_name" in row.keys() else "") or ""
        first_name = (row["first_name"] if "first_name" in row.keys() else "") or ""
        middle_name = (row["middle_name"] if "middle_name" in row.keys() else "") or ""
        if last_name.strip() or first_name.strip() or middle_name.strip():
            return last_name.strip(), first_name.strip(), middle_name.strip()
        return split_name(row["name"] or "")

    @classmethod
    def _public(cls, row: sqlite3.Row) -> dict:
        """Запись без хеша пароля — для отдачи во фронтенд."""
        last_name, first_name, middle_name = cls._fio_from_row(row)
        birth_date = (row["birth_date"] if "birth_date" in row.keys() else "") or ""
        return {
            "id": row["id"],
            "name": row["name"],
            "login": row["login"],
            "position": row["position"],
            "phone": row["phone"],
            "active": bool(row["active"]),
            "created_at": row["created_at"],
            "last_name": last_name,
            "first_name": first_name,
            "middle_name": middle_name,
            "birth_date": birth_date.strip(),
        }

    def list_all(self) -> list[dict]:
        with self._conn() as c:
            rows = c.execute(
                "SELECT * FROM employees ORDER BY active DESC, name COLLATE NOCASE"
            ).fetchall()
        return [self._public(r) for r in rows]

    def active_names(self) -> list[str]:
        with self._conn() as c:
            rows = c.execute(
                "SELECT name FROM employees WHERE active=1 ORDER BY name COLLATE NOCASE"
            ).fetchall()
        return [r["name"] for r in rows]

    def count(self) -> int:
        with self._conn() as c:
            return int(c.execute("SELECT COUNT(*) AS n FROM employees").fetchone()["n"])

    def get(self, emp_id: int) -> dict | None:
        with self._conn() as c:
            row = c.execute("SELECT * FROM employees WHERE id=?", (emp_id,)).fetchone()
        return self._public(row) if row else None

    def get_by_login(self, login: str) -> sqlite3.Row | None:
        with self._conn() as c:
            return c.execute(
                "SELECT * FROM employees WHERE login=? COLLATE NOCASE", ((login or "").strip(),)
            ).fetchone()

    # ---- профиль текущего пользователя ------------------------------------

    def get_profile_by_sub(self, sub: str, fallback_name: str = "") -> dict:
        sub = (sub or "").strip()
        if sub.startswith("emp:"):
            try:
                emp_id = int(sub.split(":", 1)[1])
            except ValueError:
                emp_id = 0
            emp = self.get(emp_id) if emp_id else None
            if emp:
                return {
                    "sub": sub,
                    "login": emp.get("login") or "",
                    "last_name": emp.get("last_name") or "",
                    "first_name": emp.get("first_name") or "",
                    "middle_name": emp.get("middle_name") or "",
                    "birth_date": emp.get("birth_date") or "",
                    "phone": emp.get("phone") or "",
                    "name": emp.get("name") or "",
                    "editable": True,
                }

        with self._conn() as c:
            row = c.execute("SELECT * FROM user_profiles WHERE sub=?", (sub,)).fetchone()
        if row:
            name = compose_name(row["last_name"], row["first_name"], row["middle_name"]) or fallback_name
            return {
                "sub": sub,
                "login": "",
                "last_name": row["last_name"] or "",
                "first_name": row["first_name"] or "",
                "middle_name": row["middle_name"] or "",
                "birth_date": row["birth_date"] or "",
                "phone": row["phone"] or "",
                "name": name,
                "editable": True,
            }

        last_name, first_name, middle_name = split_name(fallback_name)
        return {
            "sub": sub,
            "login": "",
            "last_name": last_name,
            "first_name": first_name,
            "middle_name": middle_name,
            "birth_date": "",
            "phone": "",
            "name": fallback_name or compose_name(last_name, first_name, middle_name),
            "editable": True,
        }

    def update_profile_by_sub(
        self,
        sub: str,
        *,
        last_name: str = "",
        first_name: str = "",
        middle_name: str = "",
        birth_date: str = "",
        phone: str | None = None,
    ) -> dict:
        sub = (sub or "").strip()
        last_name = (last_name or "").strip()
        first_name = (first_name or "").strip()
        middle_name = (middle_name or "").strip()
        birth_date = (birth_date or "").strip()
        name = compose_name(last_name, first_name, middle_name)
        if not name:
            raise ValueError("Укажите фамилию или имя")

        if sub.startswith("emp:"):
            try:
                emp_id = int(sub.split(":", 1)[1])
            except ValueError as exc:
                raise ValueError("Профиль недоступен") from exc
            if self.get(emp_id) is None:
                raise ValueError("Сотрудник не найден")
            sets = [
                "last_name=?",
                "first_name=?",
                "middle_name=?",
                "birth_date=?",
                "name=?",
                "updated_at=?",
            ]
            args: list = [last_name, first_name, middle_name, birth_date, name, time.time()]
            if phone is not None:
                sets.append("phone=?")
                args.append(phone.strip())
            args.append(emp_id)
            with self._conn() as c:
                c.execute(f"UPDATE employees SET {', '.join(sets)} WHERE id=?", args)
            return self.get_profile_by_sub(sub, name)

        now = time.time()
        phone_val = (phone or "").strip() if phone is not None else None
        with self._conn() as c:
            existing = c.execute("SELECT phone FROM user_profiles WHERE sub=?", (sub,)).fetchone()
            if phone_val is None:
                phone_val = (existing["phone"] if existing else "") or ""
            c.execute(
                """
                INSERT INTO user_profiles
                    (sub, last_name, first_name, middle_name, birth_date, phone, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(sub) DO UPDATE SET
                    last_name=excluded.last_name,
                    first_name=excluded.first_name,
                    middle_name=excluded.middle_name,
                    birth_date=excluded.birth_date,
                    phone=excluded.phone,
                    updated_at=excluded.updated_at
                """,
                (sub, last_name, first_name, middle_name, birth_date, phone_val, now),
            )
        return self.get_profile_by_sub(sub, name)

    # ---- запись -------------------------------------------------------------

    def create(self, name: str, login: str, password: str,
               position: str = "", phone: str = "") -> dict:
        name = (name or "").strip()
        login = (login or "").strip()
        if not name or not login or not password:
            raise ValueError("Укажите имя, логин и пароль")
        if self.get_by_login(login) is not None:
            raise ValueError("Логин уже занят")
        last_name, first_name, middle_name = split_name(name)
        now = time.time()
        with self._conn() as c:
            cur = c.execute(
                "INSERT INTO employees(name, login, password_hash, position, phone, "
                "last_name, first_name, middle_name, birth_date, "
                "active, created_at, updated_at) VALUES(?,?,?,?,?,?,?,?,?,1,?,?)",
                (name, login, hash_password(password), (position or "").strip(),
                 (phone or "").strip(), last_name, first_name, middle_name, "", now, now),
            )
            emp_id = cur.lastrowid
        return self.get(emp_id)  # type: ignore[return-value]

    def update(self, emp_id: int, *, name: str | None = None, login: str | None = None,
               password: str | None = None, position: str | None = None,
               phone: str | None = None, active: bool | None = None) -> dict | None:
        if self.get(emp_id) is None:
            return None
        sets: list[str] = []
        args: list = []
        if name is not None and name.strip():
            last_name, first_name, middle_name = split_name(name)
            sets.append("name=?"); args.append(name.strip())
            sets.append("last_name=?"); args.append(last_name)
            sets.append("first_name=?"); args.append(first_name)
            sets.append("middle_name=?"); args.append(middle_name)
        if login is not None and login.strip():
            other = self.get_by_login(login)
            if other is not None and int(other["id"]) != int(emp_id):
                raise ValueError("Логин уже занят")
            sets.append("login=?"); args.append(login.strip())
        if password:
            sets.append("password_hash=?"); args.append(hash_password(password))
        if position is not None:
            sets.append("position=?"); args.append(position.strip())
        if phone is not None:
            sets.append("phone=?"); args.append(phone.strip())
        if active is not None:
            sets.append("active=?"); args.append(1 if active else 0)
        if not sets:
            return self.get(emp_id)
        sets.append("updated_at=?"); args.append(time.time())
        args.append(emp_id)
        with self._conn() as c:
            c.execute(f"UPDATE employees SET {', '.join(sets)} WHERE id=?", args)
        return self.get(emp_id)

    def delete(self, emp_id: int) -> bool:
        with self._conn() as c:
            cur = c.execute("DELETE FROM employees WHERE id=?", (emp_id,))
            return cur.rowcount > 0

    def authenticate(self, login: str, password: str) -> dict | None:
        """Проверяет логин/пароль сотрудника. Возвращает {sub, name, role} или None."""
        row = self.get_by_login(login)
        if row is None or not int(row["active"]):
            return None
        if not verify_password(password, row["password_hash"]):
            return None
        return {
            "sub": f"emp:{row['id']}",
            "name": row["name"],
            "role": "Сотрудник",
        }
