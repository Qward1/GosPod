"""HTTP API авторизации веб-кабинета (ЕСИА-стиль, Ory Kratos / demo).

Эндпоинты под /api/web/auth/* — НЕ защищены сессией (иначе нельзя было бы войти).
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel

from app.auth.deps import current_user, require_user
from app.auth.service import COOKIE_NAME, AuthBackendUnavailable, AuthService, is_admin_role

router = APIRouter(prefix="/api/web/auth", tags=["auth"])


def _svc(request: Request) -> AuthService:
    svc = getattr(request.app.state, "auth", None)
    if svc is None:
        raise HTTPException(status_code=503, detail="Авторизация не инициализирована")
    return svc


class LoginBody(BaseModel):
    identifier: str
    password: str


class ProfileUpdateBody(BaseModel):
    last_name: str = ""
    first_name: str = ""
    middle_name: str = ""
    birth_date: str = ""
    phone: str | None = None


@router.get("/config")
async def auth_config(request: Request) -> dict:
    """Публичная информация для логин-экрана (без секретов)."""
    svc = _svc(request)
    return {
        "enabled": svc.auth.enabled,
        "mode": svc.auth.mode,
        "title": request.app.state.cfg.web.title,
    }


@router.get("/whoami")
async def whoami(request: Request) -> dict:
    user = current_user(request)
    if user is None:
        raise HTTPException(status_code=401, detail="Не авторизован")
    return {"user": {
        "name": user.get("name"),
        "role": user.get("role"),
        "sub": user.get("sub"),
        "is_admin": is_admin_role(user.get("role")),
    }}


@router.get("/profile")
async def get_profile(request: Request) -> dict:
    """Профиль владельца сессии (ФИО по частям, дата рождения, телефон)."""
    user = require_user(request)
    svc = _svc(request)
    profile = svc.employees.get_profile_by_sub(
        str(user.get("sub") or ""),
        fallback_name=str(user.get("name") or ""),
    )
    return {"profile": profile}


@router.put("/profile")
async def update_profile(request: Request, body: ProfileUpdateBody, response: Response) -> dict:
    """Сохраняет профиль и ПЕРЕВЫПУСКАЕТ cookie-сессию с новым отображаемым именем.

    Имя лежит внутри подписанного токена, поэтому без перевыпуска шапка кабинета
    и подпись ответов показывали бы старое ФИО до следующего входа.
    """
    user = require_user(request)
    svc = _svc(request)
    try:
        profile = svc.employees.update_profile_by_sub(
            str(user.get("sub") or ""),
            last_name=body.last_name,
            first_name=body.first_name,
            middle_name=body.middle_name,
            birth_date=body.birth_date,
            phone=body.phone,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None

    name = profile.get("name") or user.get("name")
    role = user.get("role") or "Оператор"
    token = svc.issue_token({"sub": user.get("sub"), "name": name, "role": role})
    response.set_cookie(
        COOKIE_NAME, token,
        max_age=svc.auth.session_ttl_hours * 3600,
        httponly=True, samesite="lax", path="/",
    )
    return {
        "ok": True,
        "profile": profile,
        "user": {
            "name": name,
            "role": role,
            "sub": user.get("sub"),
            "is_admin": is_admin_role(role),
        },
    }


@router.post("/login")
async def login(request: Request, body: LoginBody, response: Response) -> dict:
    svc = _svc(request)
    if not svc.auth.enabled:
        return {"ok": True, "user": {"name": "Оператор", "role": "Оператор"}}
    try:
        user = await svc.authenticate(body.identifier, body.password)
    except AuthBackendUnavailable:
        raise HTTPException(status_code=503, detail="Сервис авторизации временно недоступен") from None
    if not user:
        raise HTTPException(status_code=401, detail="Неверный логин или пароль")
    token = svc.issue_token(user)
    response.set_cookie(
        COOKIE_NAME, token,
        max_age=svc.auth.session_ttl_hours * 3600,
        httponly=True, samesite="lax", path="/",
    )
    return {"ok": True, "user": {"name": user["name"], "role": user["role"]}}


@router.post("/logout")
async def logout(response: Response) -> dict:
    response.delete_cookie(COOKIE_NAME, path="/")
    return {"ok": True}
