"""Зависимости FastAPI для проверки сессии веб-кабинета."""
from __future__ import annotations

from fastapi import HTTPException, Request

from app.auth.service import COOKIE_NAME, AuthService, is_admin_role


def _auth(request: Request) -> AuthService | None:
    return getattr(request.app.state, "auth", None)


def current_user(request: Request) -> dict | None:
    """Возвращает данные пользователя из cookie-сессии (или None)."""
    svc = _auth(request)
    if svc is None or not svc.auth.enabled:
        # Авторизация отключена — пускаем как анонимного оператора.
        return {"sub": "anonymous", "name": "Оператор", "role": "Оператор"}
    if (svc.auth.mode or "").strip().lower() == "external":
        # Доступ уже проверен внешним SSO/reverse proxy. Не требуем вторую
        # внутреннюю сессию cc_session, которой у OAuth-платформы нет.
        return {
            "sub": "external-sso",
            "name": svc.auth.external_name,
            "role": svc.auth.external_role,
        }
    token = request.cookies.get(COOKIE_NAME)
    return svc.verify_token(token)


def require_user(request: Request) -> dict:
    """Гейт для защищённых эндпоинтов: 401, если нет валидной сессии."""
    user = current_user(request)
    if user is None:
        raise HTTPException(status_code=401, detail="Требуется авторизация")
    return user


def require_admin(request: Request) -> dict:
    """Гейт для админских эндпоинтов: 401 без сессии, 403 если не администратор."""
    user = require_user(request)
    if not is_admin_role(user.get("role")):
        raise HTTPException(status_code=403, detail="Недостаточно прав")
    return user
