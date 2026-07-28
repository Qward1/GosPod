"""Эндпоинт извлечения данных из Excel — вызывается из Dify HTTP-узла (задание 1)."""
from __future__ import annotations

import httpx
from fastapi import APIRouter, Depends, File, Form, Header, HTTPException, Request, UploadFile

from app.config import Config, get_config
from app.excel.extractor import extract_xlsx

router = APIRouter(prefix="/excel", tags=["excel"])


def require_api_key(
    x_api_key: str | None = Header(default=None, alias="X-Api-Key"),
    cfg: Config = Depends(get_config),
) -> None:
    expected = cfg.server.api_key
    # Если ключ в конфиге не задан — проверку не навязываем (удобно для локалки).
    if expected and x_api_key != expected:
        raise HTTPException(status_code=401, detail="Неверный или отсутствующий X-Api-Key")


async def _resolve_bytes(
    request: Request, file: UploadFile | None, file_url: str | None
) -> bytes:
    if file is not None:
        return await file.read()

    # file_url может прийти как query-параметр или поле формы.
    url = file_url or request.query_params.get("file_url")
    if url:
        async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
            resp = await client.get(url)
            if resp.status_code >= 400:
                raise HTTPException(
                    status_code=400, detail=f"Не удалось скачать файл по file_url: {resp.status_code}"
                )
            return resp.content

    # Иначе пробуем сырое тело запроса.
    return await request.body()


@router.post("/extract")
async def extract(
    request: Request,
    file: UploadFile | None = File(default=None, description="Excel-файл (.xlsx)"),
    file_url: str | None = Form(default=None, description="URL файла (альтернатива загрузке)"),
    question: str | None = Form(default=None, description="Необязательный вопрос пользователя"),
    cfg: Config = Depends(get_config),
    _: None = Depends(require_api_key),
):
    """Принимает Excel и возвращает компактный текст всех записей."""
    content = await _resolve_bytes(request, file, file_url)
    if not content:
        raise HTTPException(status_code=400, detail="Файл не передан (file / file_url / тело пустые)")

    try:
        result = extract_xlsx(
            content,
            max_chars=cfg.excel.max_chars,
            drop_empty_cells=cfg.excel.drop_empty_cells,
        )
    except Exception as exc:  # noqa: BLE001 — отдаём понятную ошибку наружу
        raise HTTPException(status_code=422, detail=f"Не удалось разобрать Excel: {exc}") from exc

    return {
        "text": result.text,
        "rows": result.rows,
        "total_rows": result.total_rows,
        "columns": result.columns,
        "char_count": result.char_count,
        "token_estimate": result.token_estimate,
        "truncated": result.truncated,
        "question": question,
    }
