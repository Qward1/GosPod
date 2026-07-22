"""HTTP API админ-функций кабинета: push-рассылки пользователям MAX и аудит-лог.

Всё под /api/web/* и защищено `require_admin` (только администратор). Монтируется
дважды (с/без префикса /application), как остальные веб-роутеры (см. main.py).
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from app.auth.deps import require_admin
from app.web.service import WebService

router = APIRouter(prefix="/api/web", tags=["web-admin"],
                   dependencies=[Depends(require_admin)])


def _svc(request: Request) -> WebService:
    svc = getattr(request.app.state, "web", None)
    if svc is None:
        raise HTTPException(status_code=503, detail="Веб-кабинет отключён (web.enabled=false)")
    return svc


class BroadcastBody(BaseModel):
    text: str
    target: str = "all"  # all | subscribers


@router.get("/broadcast/audience")
async def broadcast_audience(request: Request) -> dict:
    """Размер аудитории каждой рассылки + готовность MAX-бота (для UI)."""
    return _svc(request).broadcast_audience()


@router.post("/broadcast")
async def broadcast(request: Request, body: BroadcastBody,
                    user: dict = Depends(require_admin)) -> dict:
    text = (body.text or "").strip()
    if not text:
        raise HTTPException(status_code=400, detail="Введите текст сообщения")
    target = body.target if body.target in ("all", "subscribers") else "all"
    res = await _svc(request).broadcast(text, target=target, actor=user)
    if not res.get("ok"):
        raise HTTPException(status_code=400, detail=res.get("error") or "Не удалось отправить")
    return res


@router.get("/audit")
async def audit(request: Request, limit: int = 300, entity: str = "", action: str = "") -> dict:
    limit = max(1, min(2000, int(limit or 300)))
    return {"items": _svc(request).list_audit(limit=limit, entity=entity, action=action)}


class SlaBody(BaseModel):
    sla_business_days: int


@router.get("/settings/sla")
async def get_sla(request: Request) -> dict:
    """Текущий регламент времени ответа на обращения (календарных дней)."""
    return _svc(request).get_sla_settings()


@router.put("/settings/sla")
async def update_sla(request: Request, body: SlaBody,
                     user: dict = Depends(require_admin)) -> dict:
    try:
        return _svc(request).set_sla_business_days(body.sla_business_days, actor=user)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None
