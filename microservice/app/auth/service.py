"""Авторизация веб-кабинета в стиле ЕСИА на базе Ory Kratos.

Вход устроен как «свой логин-экран → наш бэкенд проверяет учётку → ставим
подписанную cookie-сессию». Сама проверка учётных данных делается одним из двух
способов (config `auth.mode`):

  • demo   — по локальному списку `auth.demo_users` (работает сразу, без Kratos);
  • kratos — через Identity API Kratos (self-service login flow типа `api`),
             что не требует браузерных redirect/CSRF;
  • external — вход выполняет внешний SSO/reverse proxy, поэтому собственная
               cookie-сессия кабинета не проверяется.

Cookie-сессия кабинета — собственная (HMAC-SHA256 по `auth.session_secret`), не
зависит от cookie Kratos: это позволяет одинаково гейтить и demo-, и kratos-режим
и не пробрасывать домены. Всё разворачивается на сервере (deploy/kratos/).
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import time

import httpx

from app.auth.employees import EmployeeStore
from app.config import Config

log = logging.getLogger("auth")


def is_admin_role(role: str | None) -> bool:
    """Единая трактовка роли: админ ↔ всё остальное (сотрудник).

    Учётки сотрудников создаёт админ в «Настройках»; они всегда не-админы.
    Админом считается роль с «админ»/«admin» в названии (демо-учётка из конфига).
    """
    r = (role or "").strip().lower()
    return "админ" in r or "admin" in r


class AuthBackendUnavailable(RuntimeError):
    pass


class AuthService:
    def __init__(self, cfg: Config, employees: EmployeeStore | None = None):
        self.cfg = cfg
        self.auth = cfg.auth
        # Отдельная БД сотрудников (создаётся администратором в «Настройках»).
        self.employees = employees or EmployeeStore(self.auth.employees_db)
        mode = (self.auth.mode or "demo").strip().lower()
        log.info("Auth enabled=%s mode=%s demo_users=%d employees=%d",
                 self.auth.enabled, mode, len(self.auth.demo_users or []),
                 self.employees.count())

    # ---- проверка учётных данных -------------------------------------------

    async def authenticate(self, identifier: str, password: str) -> dict | None:
        """Проверяет логин/пароль. Возвращает {sub, name, role} или None."""
        identifier = (identifier or "").strip()
        password = password or ""
        if not identifier or not password:
            return None
        # Сотрудники (созданные администратором) проверяются по отдельной БД —
        # независимо от выбранного режима demo/kratos для административных учёток.
        emp = self.employees.authenticate(identifier, password)
        if emp is not None:
            return emp
        mode = (self.auth.mode or "demo").strip().lower()
        if mode == "kratos":
            user = await self._authenticate_kratos(identifier, password)
        else:
            user = self._authenticate_demo(identifier, password)
        return self._apply_saved_profile(user)

    def _apply_saved_profile(self, user: dict | None) -> dict | None:
        """Подставляет ФИО, сохранённое пользователем в «Настройках».

        Имя для demo/Kratos-учёток приходит из конфига (или из identity-трейтов)
        и при каждом входе перекрывало бы профиль, отредактированный в кабинете.
        Профиль сотрудника лежит в его же строке `employees`, поэтому там
        подмена не нужна.
        """
        if not user:
            return user
        sub = str(user.get("sub") or "")
        if not sub or sub.startswith("emp:"):
            return user
        try:
            profile = self.employees.get_profile_by_sub(sub, fallback_name=str(user.get("name") or ""))
        except Exception:  # noqa: BLE001 — профиль не критичен для входа
            log.warning("Не удалось прочитать профиль sub=%s", sub, exc_info=True)
            return user
        name = (profile.get("name") or "").strip()
        if name:
            user = {**user, "name": name}
        return user

    def _authenticate_demo(self, identifier: str, password: str) -> dict | None:
        users = self.auth.demo_users or []
        if not users:
            log.warning("Demo auth is enabled, but auth.demo_users is empty")
            return None
        for u in users:
            user_identifier = str(
                u.get("identifier") or u.get("login") or u.get("username") or u.get("email") or ""
            ).strip().lower()
            user_password = str(u.get("password", "")).strip()
            if user_identifier == identifier.lower() and user_password == password.strip():
                return {
                    "sub": identifier,
                    "name": u.get("name") or identifier,
                    "role": u.get("role") or "Оператор",
                }
        log.info("Demo auth rejected identifier=%s", identifier)
        return None

    async def _authenticate_kratos(self, identifier: str, password: str) -> dict | None:
        base = (self.auth.kratos_public_url or "").rstrip("/")
        try:
            async with httpx.AsyncClient(timeout=20.0) as client:
                flow = await client.get(
                    f"{base}/self-service/login/api",
                    headers={"Accept": "application/json"},
                )
                if flow.status_code >= 400:
                    log.error("Kratos login flow error %s: %s", flow.status_code, flow.text[:300])
                    raise AuthBackendUnavailable("Kratos login flow is unavailable")
                flow_id = flow.json().get("id")
                if not flow_id:
                    log.error("Kratos login flow response does not contain id")
                    raise AuthBackendUnavailable("Kratos login flow response is invalid")
                resp = await client.post(
                    f"{base}/self-service/login",
                    params={"flow": flow_id},
                    headers={"Accept": "application/json", "Content-Type": "application/json"},
                    json={"method": "password", "identifier": identifier, "password": password},
                )
        except Exception as exc:  # noqa: BLE001
            if isinstance(exc, AuthBackendUnavailable):
                raise
            log.warning("Kratos недоступен (%s) — вход временно невозможен", exc)
            raise AuthBackendUnavailable("Kratos is unavailable") from exc

        if resp.status_code >= 400:
            if resp.status_code >= 500:
                log.error("Kratos login error %s: %s", resp.status_code, resp.text[:300])
                raise AuthBackendUnavailable("Kratos login failed")
            log.info("Kratos отклонил вход для %s: %s", identifier, resp.status_code)
            return None
        data = resp.json()
        identity = (data.get("session") or {}).get("identity") or {}
        traits = identity.get("traits") or {}
        name = traits.get("name")
        if isinstance(name, dict):
            name = " ".join(filter(None, [name.get("first"), name.get("last")]))
        return {
            "sub": identity.get("id") or identifier,
            "name": name or traits.get("email") or identifier,
            "role": traits.get("role") or "Оператор",
        }

    # ---- cookie-сессия (HMAC) ----------------------------------------------

    def issue_token(self, user: dict) -> str:
        payload = {
            "sub": user["sub"],
            "name": user["name"],
            "role": user.get("role", "Оператор"),
            "exp": int(time.time()) + self.auth.session_ttl_hours * 3600,
        }
        raw = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        body = base64.urlsafe_b64encode(raw).rstrip(b"=")
        sig = self._sign(body)
        return f"{body.decode()}.{sig}"

    def verify_token(self, token: str | None) -> dict | None:
        if not token or "." not in token:
            return None
        body, _, sig = token.rpartition(".")
        if not hmac.compare_digest(sig, self._sign(body.encode())):
            return None
        try:
            padded = body + "=" * (-len(body) % 4)
            payload = json.loads(base64.urlsafe_b64decode(padded))
        except Exception:  # noqa: BLE001
            return None
        if int(payload.get("exp", 0)) < int(time.time()):
            return None
        return payload

    def _sign(self, body: bytes) -> str:
        secret = (self.auth.session_secret or "change-me").encode("utf-8")
        digest = hmac.new(secret, body, hashlib.sha256).digest()
        return base64.urlsafe_b64encode(digest).rstrip(b"=").decode()


COOKIE_NAME = "cc_session"
