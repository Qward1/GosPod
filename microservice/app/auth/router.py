"""HTTP API авторизации веб-кабинета (ЕСИА-стиль, Ory Kratos / demo).

Эндпоинты под /api/web/auth/* — НЕ защищены сессией (иначе нельзя было бы войти).
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel

from app.auth.deps import current_user
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
