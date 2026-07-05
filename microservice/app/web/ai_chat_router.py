"""HTTP API раздела «Чат с ИИ» и админской пересборки базы знаний УСВО."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel

from app.auth.deps import require_admin, require_user
from app.web.ai_chat import (
    ANSWER_TYPES,
    AiChatNotFound,
    AiChatProviderError,
    AiChatService,
    AiChatValidation,
)

router = APIRouter(prefix="/api/web", tags=["ai-chats"])


def _svc(request: Request) -> AiChatService:
    service = getattr(request.app.state, "ai_chat", None)
    if service is None:
        raise HTTPException(status_code=503, detail="Чат с ИИ не инициализирован")
    return service


def _user_id(user: dict) -> str:
    return str(user.get("sub") or user.get("name") or "anonymous")


class ChatCreateBody(BaseModel):
    title: str = ""
    answerType: str = "text"


class ChatUpdateBody(BaseModel):
    title: str | None = None
    answerType: str | None = None


class MessageCreateBody(BaseModel):
    content: str
    answerType: str | None = None


def _raise_known(exc: RuntimeError) -> None:
    if isinstance(exc, AiChatNotFound):
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    if isinstance(exc, AiChatValidation):
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if isinstance(exc, AiChatProviderError):
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    raise exc


@router.get("/ai-chats")
async def list_chats(request: Request, user: dict = Depends(require_user)) -> dict:
    service = _svc(request)
    return {
        "items": service.list_chats(_user_id(user)),
        "answerTypes": [{"value": key, "label": label} for key, label in ANSWER_TYPES.items()],
        "ready": service.ready(),
    }


@router.post("/ai-chats")
async def create_chat(
    request: Request, body: ChatCreateBody, user: dict = Depends(require_user)
) -> dict:
    try:
        return {"chat": _svc(request).create_chat(
            _user_id(user), body.title, body.answerType
        )}
    except RuntimeError as exc:
        _raise_known(exc)


@router.get("/ai-chats/{chat_id}")
async def get_chat(
    request: Request, chat_id: int, user: dict = Depends(require_user)
) -> dict:
    try:
        return {"chat": _svc(request).get_chat(_user_id(user), chat_id)}
    except RuntimeError as exc:
        _raise_known(exc)


@router.patch("/ai-chats/{chat_id}")
async def update_chat(
    request: Request,
    chat_id: int,
    body: ChatUpdateBody,
    user: dict = Depends(require_user),
) -> dict:
    try:
        return {"chat": _svc(request).update_chat(
            _user_id(user),
            chat_id,
            title=body.title,
            answer_type=body.answerType,
        )}
    except RuntimeError as exc:
        _raise_known(exc)


@router.delete("/ai-chats/{chat_id}")
async def delete_chat(
    request: Request, chat_id: int, user: dict = Depends(require_user)
) -> dict:
    try:
        _svc(request).delete_chat(_user_id(user), chat_id)
        return {"ok": True}
    except RuntimeError as exc:
        _raise_known(exc)


@router.get("/ai-chats/{chat_id}/messages")
async def list_messages(
    request: Request, chat_id: int, user: dict = Depends(require_user)
) -> dict:
    try:
        return {"items": _svc(request).list_messages(_user_id(user), chat_id)}
    except RuntimeError as exc:
        _raise_known(exc)


@router.get("/ai-chats/{chat_id}/messages/{message_id}/docx")
async def message_docx(
    request: Request,
    chat_id: int,
    message_id: int,
    user: dict = Depends(require_user),
) -> Response:
    try:
        data, filename = _svc(request).message_docx(
            _user_id(user), chat_id, message_id
        )
    except RuntimeError as exc:
        _raise_known(exc)
    return Response(
        content=data,
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.post("/ai-chats/{chat_id}/messages")
async def send_message(
    request: Request,
    chat_id: int,
    body: MessageCreateBody,
    user: dict = Depends(require_user),
) -> dict:
    try:
        return await _svc(request).send_message(
            _user_id(user), chat_id, body.content, body.answerType
        )
    except RuntimeError as exc:
        _raise_known(exc)


@router.post("/ai-knowledge/usvo/rebuild")
async def rebuild_usvo_knowledge(
    request: Request, _admin: dict = Depends(require_admin)
) -> dict:
    result = await _svc(request).knowledge.rebuild()
    if not result.get("ok"):
        raise HTTPException(
            status_code=502 if result.get("errors") else 400,
            detail=result.get("error") or "Не удалось пересобрать базу знаний УСВО",
        )
    return result
