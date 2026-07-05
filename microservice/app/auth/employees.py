"""Хранилище учётных записей сотрудников (роль «Сотрудник») в ОТДЕЛЬНОЙ БД.

По заданию аккаунты сотрудников, которые создаёт администратор, держатся отдельно
от состояния бота (`state.db`) — в своём файле `data/employees.db` (путь —
`auth.employees_db`). Пароли не хранятся в открытом виде: PBKDF2-HMAC-SHA256 с
индивидуальной солью на каждую запись.

Используется только stdlib sqlite3 + hashlib. Соединение открывается на каждую
операцию — нагрузка кабинета это с запасом выдерживает.
"""
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

    # ---- чтение -------------------------------------------------------------

    @staticmethod
    def _public(row: sqlite3.Row) -> dict:
        """Запись без хеша пароля — для отдачи во фронтенд."""
        return {
            "id": row["id"],
            "name": row["name"],
            "login": row["login"],
            "position": row["position"],
            "phone": row["phone"],
            "active": bool(row["active"]),
            "created_at": row["created_at"],
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

    # ---- запись -------------------------------------------------------------

    def create(self, name: str, login: str, password: str,
               position: str = "", phone: str = "") -> dict:
        name = (name or "").strip()
        login = (login or "").strip()
        if not name or not login or not password:
            raise ValueError("Укажите имя, логин и пароль")
        if self.get_by_login(login) is not None:
            raise ValueError("Логин уже занят")
        now = time.time()
        with self._conn() as c:
            cur = c.execute(
                "INSERT INTO employees(name, login, password_hash, position, phone, "
                "active, created_at, updated_at) VALUES(?,?,?,?,?,1,?,?)",
                (name, login, hash_password(password), (position or "").strip(),
                 (phone or "").strip(), now, now),
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
            sets.append("name=?"); args.append(name.strip())
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
