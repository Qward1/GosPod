"""HTTP API точного поиска по карточкам УСВО (вопрос/фильтр → SQLite-индекс)."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from app.auth.deps import require_user
from app.web.usvo_index import field_catalog, validate_filter
from app.web.usvo_query import format_db_result, plan_filter

router = APIRouter(prefix="/api/web", tags=["usvo-query"])


class QueryBody(BaseModel):
    question: str | None = None
    filter: dict | None = None
    limit: int | None = None
    include_cards: bool = True


def _svc(request: Request):
    service = getattr(request.app.state, "ai_chat", None)
    if service is None or getattr(service, "index", None) is None:
        raise HTTPException(status_code=503, detail="Поиск по карточкам не инициализирован")
    return service


def _card_dict(record, deep_link: str) -> dict:
    return {
        "id": record.id,
        "name": record.name,
        "short_name": record.short_name,
        "status": record.status,
        "phone": record.phone,
        "address": record.address,
        "awards": record.awards,
        "source": record.source,
        "url": deep_link,
    }


@router.get("/usvo/query/catalog")
async def query_catalog(_user: dict = Depends(require_user)) -> dict:
    return field_catalog()


@router.post("/usvo/query")
async def query_usvo(
    request: Request, body: QueryBody, _user: dict = Depends(require_user)
) -> dict:
    service = _svc(request)

    if body.filter is not None:
        raw = body.filter
    elif body.question and body.question.strip():
        raw = await plan_filter(body.question.strip(), service.planner, service.meta)
    else:
        raise HTTPException(status_code=400, detail="Нужен либо question, либо filter")

    clean, warnings = validate_filter(raw)
    if body.limit and body.limit > 0:
        clean["limit"] = min(int(body.limit), 200)

    result = service.index.execute(clean)
    records = service.records_provider()
    by_id = {r.id: r for r in records}

    cards = []
    if body.include_cards:
        for cid in result["card_ids"]:
            rec = by_id.get(cid)
            if rec is not None:
                cards.append(_card_dict(rec, f"/usvo/cards/{rec.id}"))

    return {
        "filter": clean,
        "intent": result["intent"],
        "total": result["total"],
        "aggregate": result["aggregate"],
        "aggregate_by": result["aggregate_by"],
        "warnings": warnings,
        "cards": cards,
        "text": format_db_result(body.question or "", clean, result, records),
    }
