"""HTTP API подраздела «Меры поддержки» (внутри раздела «Заявления»).

Доступен ТОЛЬКО администратору (`require_admin`). Эндпоинты под
/api/web/settings/support-measures/*. Данные — таблица support_measures (см. store.py),
шаблоны — каталог dify.measure_templates_dir. После мутаций описание активных мер
синхронизируется в базу знаний (best-effort).
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, File, HTTPException, Request, Response, UploadFile
from pydantic import BaseModel

from app.auth.deps import require_admin
from app.web.service import WebService

router = APIRouter(
    prefix="/api/web/settings",
    tags=["support-measures"],
    dependencies=[Depends(require_admin)],
)

_DOCX_MIME = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


def _svc(request: Request) -> WebService:
    svc = getattr(request.app.state, "web", None)
    if svc is None:
        raise HTTPException(status_code=503, detail="Веб-кабинет отключён (web.enabled=false)")
    return svc


class PlaceholderIn(BaseModel):
    key: str
    label: str = ""


class SupportMeasureIn(BaseModel):
    title: str
    description: str = ""
    documents: list[str] = []
    placeholders: list[PlaceholderIn] = []
    llm_hint: str = ""
    category: str = ""
    active: bool = True


class SupportMeasureUpdate(BaseModel):
    title: str | None = None
    description: str | None = None
    documents: list[str] | None = None
    placeholders: list[PlaceholderIn] | None = None
    llm_hint: str | None = None
    category: str | None = None
    active: bool | None = None


def _payload(body: SupportMeasureIn | SupportMeasureUpdate) -> dict:
    data = body.model_dump(exclude_none=True)
    if "placeholders" in data and data["placeholders"] is not None:
        data["placeholders"] = [
            {"key": p["key"], "label": p.get("label", "")} for p in data["placeholders"]
        ]
    return data


@router.get("/support-measures")
async def list_measures(request: Request) -> dict:
    return {"items": _svc(request).list_support_measures()}


@router.get("/support-measures/{measure_id}")
async def get_measure(request: Request, measure_id: int) -> dict:
    m = _svc(request).get_support_measure(measure_id)
    if not m:
        raise HTTPException(status_code=404, detail="Мера поддержки не найдена")
    return {"measure": m}


@router.post("/support-measures")
async def create_measure(request: Request, body: SupportMeasureIn) -> dict:
    svc = _svc(request)
    res = svc.create_support_measure(_payload(body))
    if not res.get("ok"):
        raise HTTPException(status_code=400, detail=res.get("error", "Ошибка создания"))
    await svc.sync_measure_to_kb(res["measure"]["id"])  # автосинхронизация с базой знаний
    return res


@router.put("/support-measures/{measure_id}")
async def update_measure(request: Request, measure_id: int, body: SupportMeasureUpdate) -> dict:
    svc = _svc(request)
    res = svc.update_support_measure(measure_id, _payload(body))
    if not res.get("ok"):
        raise HTTPException(status_code=404, detail=res.get("error", "Мера не найдена"))
    await svc.sync_measure_to_kb(measure_id)  # автосинхронизация (активная — обновить, иначе убрать)
    return res


@router.delete("/support-measures/{measure_id}")
async def delete_measure(request: Request, measure_id: int) -> dict:
    svc = _svc(request)
    res = svc.delete_support_measure(measure_id)
    if not res.get("ok"):
        raise HTTPException(status_code=404, detail="Мера поддержки не найдена")
    await svc.remove_measure_from_kb(measure_id)  # убрать из базы знаний
    return res


@router.post("/support-measures/{measure_id}/template")
async def upload_template(
    request: Request, measure_id: int, file: UploadFile = File(...)
) -> dict:
    raw = await file.read()
    if not raw:
        raise HTTPException(status_code=400, detail="Пустой файл")
    name = (file.filename or "").lower()
    if not name.endswith(".docx"):
        raise HTTPException(status_code=400, detail="Шаблон должен быть в формате .docx")
    svc = _svc(request)
    res = svc.save_measure_template(measure_id, file.filename or "", raw)
    if not res.get("ok"):
        raise HTTPException(status_code=400, detail=res.get("error", "Ошибка загрузки"))
    await svc.sync_measure_to_kb(measure_id)  # автосинхронизация с базой знаний
    return res


@router.get("/support-measures/{measure_id}/template")
async def download_template(request: Request, measure_id: int) -> Response:
    res = _svc(request).get_measure_template(measure_id)
    if not res:
        raise HTTPException(status_code=404, detail="Шаблон не загружен")
    content, filename = res
    return Response(
        content=content,
        media_type=_DOCX_MIME,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.post("/support-measures/sync-kb")
async def sync_kb(request: Request) -> dict:
    res = await _svc(request).sync_measures_kb()
    if not res.get("ok"):
        raise HTTPException(status_code=400, detail=res.get("error", "Не удалось синхронизировать"))
    return res
