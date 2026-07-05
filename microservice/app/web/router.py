"""HTTP API веб-кабинета «Контакт-центр» (раздаётся на том же внешнем порту).

Все эндпоинты под /api/web/*. Статические файлы интерфейса монтируются в main.py.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, File, HTTPException, Request, Response, UploadFile
from pydantic import BaseModel

from app.auth.deps import require_user
from app.web.service import WebService

# Все эндпоинты кабинета защищены сессией (см. app/auth). Логин-эндпоинты вынесены
# в отдельный роутер /api/web/auth/* и под этот гейт не попадают.
router = APIRouter(prefix="/api/web", tags=["web"], dependencies=[Depends(require_user)])


def _svc(request: Request) -> WebService:
    svc = getattr(request.app.state, "web", None)
    if svc is None:
        raise HTTPException(status_code=503, detail="Веб-кабинет отключён (web.enabled=false)")
    return svc


class AssigneeBody(BaseModel):
    assignee: str = ""


class DraftBody(BaseModel):
    operator: str = ""


class AnswerBody(BaseModel):
    answer: str
    assignee: str = ""


@router.get("/meta")
async def meta(request: Request) -> dict:
    return _svc(request).meta()


# ---- обращения -------------------------------------------------------------

@router.get("/appeals")
async def appeals(request: Request) -> dict:
    return {"items": _svc(request).list_appeals()}


@router.get("/appeals/{appeal_id}")
async def appeal(request: Request, appeal_id: str) -> dict:
    a = _svc(request).get_appeal(appeal_id)
    if not a:
        raise HTTPException(status_code=404, detail="Обращение не найдено")
    return a


@router.post("/appeals/{appeal_id}/assignee")
async def set_assignee(request: Request, appeal_id: str, body: AssigneeBody) -> dict:
    a = _svc(request).set_assignee(appeal_id, body.assignee)
    if not a:
        raise HTTPException(status_code=404, detail="Обращение не найдено")
    return a


@router.post("/appeals/{appeal_id}/draft")
async def draft(request: Request, appeal_id: str, body: DraftBody) -> dict:
    res = await _svc(request).draft(appeal_id, body.operator)
    if res.get("error") == "not_found":
        raise HTTPException(status_code=404, detail="Обращение не найдено")
    return res


@router.get("/appeals/{appeal_id}/history")
async def appeal_history(request: Request, appeal_id: str) -> dict:
    res = _svc(request).appeal_history(appeal_id)
    if res.get("error") == "not_found":
        raise HTTPException(status_code=404, detail="Обращение не найдено")
    return res


@router.post("/appeals/{appeal_id}/answer")
async def answer(request: Request, appeal_id: str, body: AnswerBody) -> dict:
    if not body.answer.strip():
        raise HTTPException(status_code=400, detail="Пустой ответ")
    res = await _svc(request).answer(appeal_id, body.answer, body.assignee)
    if res.get("error") == "not_found":
        raise HTTPException(status_code=404, detail="Обращение не найдено")
    return res


@router.delete("/appeals/{appeal_id}")
async def delete_appeal(request: Request, appeal_id: str) -> dict:
    res = _svc(request).delete_appeal(appeal_id)
    if not res.get("ok"):
        raise HTTPException(status_code=404, detail="Обращение не найдено")
    return res


# ---- карточки УСВО ---------------------------------------------------------

def _usvo_filters(
    status: str = "", vbd: str = "", employment: str = "", contact: str = "",
    org: str = "", awards: str = "", directive: str = "", source: str = "",
) -> dict:
    return {
        "status": status, "vbd": vbd, "employment": employment, "contact": contact,
        "org": org, "awards": awards, "directive": directive, "source": source,
    }


@router.get("/usvo")
async def usvo_list(
    request: Request, query: str = "", status: str = "", vbd: str = "",
    employment: str = "", contact: str = "", org: str = "", awards: str = "",
    directive: str = "", source: str = "",
) -> dict:
    filters = _usvo_filters(status, vbd, employment, contact, org, awards, directive, source)
    return {"items": _svc(request).list_usvo(query, filters)}


# Шаблон и загрузка должны идти ДО маршрута с {rec_id:int}, иначе путь "template"
# попадёт в него и вернёт 422.
@router.get("/usvo/template")
async def usvo_template(request: Request) -> Response:
    data = _svc(request).usvo_template()
    return Response(
        content=data,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": 'attachment; filename="usvo-template.xlsx"'},
    )


@router.post("/usvo/import")
async def usvo_import(request: Request, file: UploadFile = File(...), replace: bool = False) -> dict:
    raw = await file.read()
    if not raw:
        raise HTTPException(status_code=400, detail="Пустой файл")
    res = await _svc(request).import_usvo(raw, replace=replace)
    if not res.get("ok"):
        raise HTTPException(status_code=400, detail=res.get("error") or "Ошибка импорта")
    return res


@router.post("/usvo/clear")
async def usvo_clear(request: Request) -> dict:
    return await _svc(request).clear_uploaded_usvo()


@router.get("/usvo/{rec_id}")
async def usvo_get(request: Request, rec_id: int) -> dict:
    r = _svc(request).get_usvo(rec_id)
    if not r:
        raise HTTPException(status_code=404, detail="Карточка не найдена")
    return r


class UsvoEditBody(BaseModel):
    fields: list[dict] = []
    history_raw: str | None = None


@router.put("/usvo/{rec_id}")
async def usvo_update(request: Request, rec_id: int, body: UsvoEditBody) -> dict:
    res = await _svc(request).update_usvo(rec_id, body.fields, body.history_raw)
    if res.get("error") == "not_found":
        raise HTTPException(status_code=404, detail="Карточка не найдена")
    if not res.get("ok"):
        raise HTTPException(status_code=400, detail=res.get("error") or "Не удалось сохранить")
    return res


@router.delete("/usvo/{rec_id}")
async def usvo_delete(request: Request, rec_id: int) -> dict:
    res = await _svc(request).delete_usvo(rec_id)
    if not res.get("ok"):
        raise HTTPException(status_code=400, detail=res.get("error") or "Не удалось удалить")
    return res


@router.get("/usvo/{rec_id}/suggestions")
async def usvo_suggestions(request: Request, rec_id: int) -> dict:
    res = await _svc(request).suggestions(rec_id)
    if res.get("error") == "not_found":
        raise HTTPException(status_code=404, detail="Карточка не найдена")
    return res


# ---- заявления (мера поддержки по фото) ------------------------------------

class DecisionBody(BaseModel):
    operator: str = ""


@router.get("/applications")
async def applications(request: Request) -> dict:
    return {"items": _svc(request).list_applications()}


@router.get("/applications/{application_id}")
async def application(request: Request, application_id: int) -> dict:
    a = _svc(request).get_application(application_id)
    if not a:
        raise HTTPException(status_code=404, detail="Заявление не найдено")
    return a


@router.post("/applications/{application_id}/approve")
async def approve_application(request: Request, application_id: int, body: DecisionBody) -> dict:
    res = _svc(request).decide_application(application_id, "approved", body.operator)
    if not res.get("ok"):
        raise HTTPException(status_code=404, detail="Заявление не найдено")
    return res


@router.post("/applications/{application_id}/reject")
async def reject_application(request: Request, application_id: int, body: DecisionBody) -> dict:
    res = _svc(request).decide_application(application_id, "rejected", body.operator)
    if not res.get("ok"):
        raise HTTPException(status_code=404, detail="Заявление не найдено")
    return res


@router.delete("/applications/{application_id}")
async def delete_application(request: Request, application_id: int) -> dict:
    res = _svc(request).delete_application(application_id)
    if not res.get("ok"):
        raise HTTPException(status_code=404, detail="Заявление не найдено")
    return res


@router.get("/applications/{application_id}/docx")
async def application_docx(request: Request, application_id: int) -> Response:
    data = _svc(request).application_docx(application_id)
    if data is None:
        raise HTTPException(status_code=404, detail="Заявление не найдено")
    return Response(
        content=data,
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        headers={"Content-Disposition": f'attachment; filename="zayavlenie-{application_id}.docx"'},
    )


# ---- аналитика -------------------------------------------------------------

@router.get("/analytics")
async def analytics(request: Request) -> dict:
    return _svc(request).analytics()


# ---- база знаний (загрузка материалов в Dify Dataset) ----------------------

class KbTextBody(BaseModel):
    title: str = ""
    text: str


@router.post("/kb/upload-text")
async def kb_upload_text(request: Request, body: KbTextBody) -> dict:
    res = await _svc(request).kb_upload_text(body.title, body.text)
    if not res.get("ok"):
        raise HTTPException(status_code=400, detail=res.get("error") or "Не удалось загрузить")
    return res


@router.post("/kb/upload-file")
async def kb_upload_file(request: Request, file: UploadFile = File(...)) -> dict:
    raw = await file.read()
    if not raw:
        raise HTTPException(status_code=400, detail="Пустой файл")
    res = await _svc(request).kb_upload_file(file.filename or "document", raw)
    if not res.get("ok"):
        raise HTTPException(status_code=400, detail=res.get("error") or "Не удалось загрузить")
    return res


# ---- выгрузка в Excel ------------------------------------------------------

_XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def _xlsx_response(data: bytes, filename: str) -> Response:
    return Response(content=data, media_type=_XLSX,
                    headers={"Content-Disposition": f'attachment; filename="{filename}"'})


@router.get("/export/usvo")
async def export_usvo(
    request: Request, query: str = "", status: str = "", vbd: str = "",
    employment: str = "", contact: str = "", org: str = "", awards: str = "",
    directive: str = "", source: str = "",
) -> Response:
    filters = _usvo_filters(status, vbd, employment, contact, org, awards, directive, source)
    return _xlsx_response(_svc(request).export_usvo(query, filters), "usvo-cards.xlsx")


@router.get("/export/appeals")
async def export_appeals(request: Request) -> Response:
    return _xlsx_response(_svc(request).export_appeals(), "appeals.xlsx")


@router.get("/export/applications")
async def export_applications(request: Request) -> Response:
    return _xlsx_response(_svc(request).export_applications(), "applications.xlsx")


@router.get("/export/analytics")
async def export_analytics(request: Request) -> Response:
    return _xlsx_response(_svc(request).export_analytics(), "analytics.xlsx")
