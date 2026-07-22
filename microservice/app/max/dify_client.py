"""Клиент Dify: вызов MAX-ветки воркфлоу и запись ответов оператора в базу знаний."""
from __future__ import annotations

import datetime as dt
import json
import logging

import httpx

log = logging.getLogger("max.dify")


class DifyApiError(RuntimeError):
    """Безопасная для API ошибка Dify без включения ключей авторизации."""


# Правила сегментации документов базы знаний (process_rule).
#
# Раньше слали {"mode": "automatic"} — а у Dify «automatic» это разделитель \n\n и
# лимит 500 токенов на чанк, поэтому длинные документы (карточки УСВО) резались на
# куски. Теперь сегментацию задаём явно:
#  • DEFAULT_PROCESS_RULE     — общий режим для всех загрузок: режем по \n\n, но
#    максимум 4000 токенов на чанк (а не 500).
#  • SINGLE_CHUNK_PROCESS_RULE — «один документ = один чанк»: разделитель-сентинел,
#    которого нет в тексте, поэтому документ не дробится (используется для карточек УСВО).
# pre_processing_rules выключены: remove_urls_emails не должен вырезать ссылку
# «/usvo/cards/<id>», remove_extra_spaces — не ломать форматирование карточки.
_PRE_PROCESSING_RULES = [
    {"id": "remove_extra_spaces", "enabled": False},
    {"id": "remove_urls_emails", "enabled": False},
]
DEFAULT_PROCESS_RULE = {
    "mode": "custom",
    "rules": {
        "pre_processing_rules": _PRE_PROCESSING_RULES,
        "segmentation": {"separator": "\n\n", "max_tokens": 4000},
    },
}
SINGLE_CHUNK_PROCESS_RULE = {
    "mode": "custom",
    "rules": {
        "pre_processing_rules": _PRE_PROCESSING_RULES,
        # Сентинел, которого заведомо нет в тексте документа → текст не дробится,
        # ограничение в 4000 токенов остаётся предохранителем для гигантских карточек.
        "segmentation": {"separator": "=====USVO_CARD_NOSPLIT=====", "max_tokens": 4000},
    },
}


def _dify_failed(res: dict) -> bool:
    """True, если ответ Dify — это ошибка (а не успешный документ).

    Успешный create/update возвращает `{"document": …, "batch": …}`; ошибки
    несут `code`/`status`/`_status` (например, 400 «Document is not available»,
    когда документ ещё индексируется, индексация упала или он архивирован).
    """
    if not isinstance(res, dict) or res.get("skipped"):
        return False
    status = res.get("status") or res.get("_status")
    if isinstance(status, int) and status >= 400:
        return True
    return bool(res.get("code"))


class DifyClient:
    def __init__(
        self,
        base_url: str,
        app_key: str,
        dataset_api_key: str,
        dataset_id: str,
        timeout: float = 60.0,
    ):
        self.base_url = base_url.rstrip("/")
        self.app_key = app_key
        self.dataset_api_key = dataset_api_key
        self.dataset_id = dataset_id
        self.timeout = timeout

    async def ask(self, query: str, user_key: str) -> dict:
        """Спрашивает MAX-ветку Dify. Ожидает, что answer-узел вернёт JSON-строку
        вида {"answer": str, "found_in_kb": bool, "confidence": number}.

        Возвращает нормализованный dict с ключами answer / found_in_kb / confidence.
        При любом сбое (не настроен ключ, сетевая ошибка/таймаут, HTTP-ошибка, битый
        JSON) возвращаем безопасный дефолт `found_in_kb=False` — тогда бот эскалирует
        вопрос оператору, а не теряет его молча. Тему при сбое считаем релевантной
        (on_topic=True), чтобы фильтр не отсёк живого человека из-за недоступности Dify.
        """
        if not _configured(self.app_key):
            log.warning("Dify app_key не настроен — вопрос будет передан оператору")
            return _ask_escalation_default()

        url = f"{self.base_url}/chat-messages"
        headers = {
            "Authorization": f"Bearer {self.app_key}",
            "Content-Type": "application/json",
        }
        body = {
            "inputs": {"channel": "max"},
            "query": query,
            "response_mode": "blocking",
            "user": user_key,
        }
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                resp = await client.post(url, headers=headers, json=body)
        except httpx.HTTPError as exc:
            # Сеть/таймаут: не роняем обработчик сообщения — эскалируем оператору.
            log.error("Dify ask request failed: %s", exc)
            return _ask_escalation_default()
        if resp.status_code >= 400:
            log.error("Dify ask error %s: %s", resp.status_code, resp.text)
            return _ask_escalation_default()
        try:
            data = resp.json()
        except json.JSONDecodeError as exc:
            log.error("Dify ask returned invalid JSON: %s", exc)
            return _ask_escalation_default()

        raw_answer = data.get("answer", "")
        parsed = _parse_structured(raw_answer)
        # Диагностика: видно, что именно вернула MAX-ветка и как это распозналось.
        # Если found_in_kb=False из-за того, что answer-узел отдал не JSON —
        # видно по raw (в нём будет текст ответа, а не {...}).
        log.info(
            "Dify ask: parsed found_in_kb=%s confidence=%.2f on_topic=%s "
            "topic_confidence=%.2f | raw_answer=%r",
            parsed["found_in_kb"], parsed["confidence"], parsed["on_topic"],
            parsed["topic_confidence"], (raw_answer or "")[:300],
        )
        return parsed

    def app_ready(self) -> bool:
        return _configured(self.app_key)

    async def ask_text(self, query: str, user_key: str, inputs: dict | None = None) -> dict:
        """Вызов отдельного Chat/Chatflow-приложения Dify с обычным текстовым ответом."""
        if not self.app_ready():
            raise DifyApiError("Dify-приложение чата не настроено")
        url = f"{self.base_url}/chat-messages"
        headers = {
            "Authorization": f"Bearer {self.app_key}",
            "Content-Type": "application/json",
        }
        body = {
            "inputs": inputs or {},
            "query": query,
            "response_mode": "blocking",
            "user": user_key,
        }
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                resp = await client.post(url, headers=headers, json=body)
        except httpx.HTTPError as exc:
            log.error("Dify chat request failed: %s", exc)
            raise DifyApiError("Не удалось подключиться к Dify") from exc
        if resp.status_code >= 400:
            log.error("Dify chat error %s: %s", resp.status_code, resp.text[:500])
            raise DifyApiError(f"Dify вернул ошибку HTTP {resp.status_code}")
        try:
            data = resp.json()
        except json.JSONDecodeError as exc:
            raise DifyApiError("Dify вернул некорректный JSON") from exc
        answer = str(data.get("answer") or "").strip()
        if not answer:
            raise DifyApiError("Dify вернул пустой ответ")
        return {
            "answer": answer,
            "conversation_id": data.get("conversation_id"),
            "message_id": data.get("message_id"),
            "metadata": data.get("metadata") or {},
        }

    def kb_ready(self) -> bool:
        """Настроена ли база знаний (Dataset API) для записи."""
        return _configured(self.dataset_id) and _configured(self.dataset_api_key)

    async def add_to_kb(self, question: str, answer: str) -> dict:
        """Записывает пару вопрос/ответ в выбранную базу знаний (Dataset API)."""
        stamp = dt.datetime.now().strftime("%Y-%m-%d %H:%M")
        return await self.add_document_text(
            f"Ответ оператора {stamp}",
            f"Вопрос: {question}\nОтвет: {answer}",
        )

    async def add_document_text(
        self, name: str, text: str, process_rule: dict | None = None
    ) -> dict:
        """Создаёт текстовый документ в базе знаний (create-by-text)."""
        if not self.kb_ready():
            log.warning("Dataset не настроен — пропускаю запись в базу знаний")
            return {"skipped": True}

        url = f"{self.base_url}/datasets/{self.dataset_id}/document/create-by-text"
        headers = {
            "Authorization": f"Bearer {self.dataset_api_key}",
            "Content-Type": "application/json",
        }
        body = {
            "name": name,
            "text": text,
            "indexing_technique": "high_quality",
            "process_rule": process_rule or DEFAULT_PROCESS_RULE,
        }
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            resp = await client.post(url, headers=headers, json=body)
            if resp.status_code >= 400:
                log.error("Dify add_document_text error %s: %s", resp.status_code, resp.text)
            try:
                return resp.json()
            except json.JSONDecodeError:
                return {"_status": resp.status_code, "_text": resp.text}

    async def list_documents(
        self, keyword: str | None = None, limit: int = 100, page: int = 1
    ) -> dict:
        """Список документов базы знаний (для идемпотентной синхронизации мер)."""
        if not self.kb_ready():
            return {"skipped": True, "data": []}
        url = f"{self.base_url}/datasets/{self.dataset_id}/documents"
        headers = {"Authorization": f"Bearer {self.dataset_api_key}"}
        params: dict = {"limit": limit, "page": page}
        if keyword:
            params["keyword"] = keyword
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            resp = await client.get(url, headers=headers, params=params)
            if resp.status_code >= 400:
                log.error("Dify list_documents error %s: %s", resp.status_code, resp.text)
                return {"data": []}
            try:
                return resp.json()
            except json.JSONDecodeError:
                return {"data": []}

    async def list_all_documents(self, keyword: str | None = None, limit: int = 100) -> list[dict]:
        """Получает все страницы документов, нужные для полной идемпотентной пересборки."""
        if not self.kb_ready():
            return []
        out: list[dict] = []
        page = 1
        while True:
            data = await self.list_documents(keyword=keyword, limit=limit, page=page)
            batch = data.get("data") or []
            out.extend(d for d in batch if isinstance(d, dict))
            if not data.get("has_more") and len(batch) < limit:
                break
            if not batch or page >= 1000:
                break
            page += 1
        return out

    async def update_document_text(
        self, document_id: str, name: str, text: str, process_rule: dict | None = None
    ) -> dict:
        """Обновляет текстовый документ базы знаний (update-by-text)."""
        if not self.kb_ready():
            return {"skipped": True}
        url = f"{self.base_url}/datasets/{self.dataset_id}/documents/{document_id}/update-by-text"
        headers = {
            "Authorization": f"Bearer {self.dataset_api_key}",
            "Content-Type": "application/json",
        }
        body = {
            "name": name,
            "text": text,
            "process_rule": process_rule or DEFAULT_PROCESS_RULE,
        }
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            resp = await client.post(url, headers=headers, json=body)
            if resp.status_code >= 400:
                log.error("Dify update_document_text error %s: %s", resp.status_code, resp.text)
            try:
                return resp.json()
            except json.JSONDecodeError:
                return {"_status": resp.status_code, "_text": resp.text}

    async def delete_document(self, document_id: str) -> dict:
        """Удаляет документ базы знаний по id."""
        if not self.kb_ready():
            return {"skipped": True}
        url = f"{self.base_url}/datasets/{self.dataset_id}/documents/{document_id}"
        headers = {"Authorization": f"Bearer {self.dataset_api_key}"}
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            resp = await client.delete(url, headers=headers)
            if resp.status_code >= 400:
                log.error("Dify delete_document error %s: %s", resp.status_code, resp.text)
            try:
                return resp.json()
            except json.JSONDecodeError:
                return {"_status": resp.status_code, "_text": resp.text}

    async def upsert_document_text(
        self,
        name: str,
        text: str,
        match_prefix: str | None = None,
        process_rule: dict | None = None,
    ) -> dict:
        """Создаёт документ или обновляет существующий (по точному имени или префиксу).

        Нужно, чтобы повторная синхронизация меры не плодила дубли в базе знаний:
        ищем документ с тем же именем (или начинающийся с match_prefix) и обновляем его.
        """
        if not self.kb_ready():
            return {"skipped": True}
        listing = await self.list_documents(keyword=match_prefix or name)
        doc_id = None
        for d in listing.get("data") or []:
            nm = (d.get("name") or "")
            if match_prefix and nm.startswith(match_prefix):
                doc_id = d.get("id")
                break
            if nm == name:
                doc_id = d.get("id")
                break
        if doc_id:
            result = await self.update_document_text(doc_id, name, text, process_rule)
            if not _dify_failed(result):
                return result
            # Старый документ недоступен для обновления (индексируется/ошибка/
            # архив → 400 «Document is not available») — пересоздаём его заново.
            log.warning(
                "update-by-text не удалось для %r, удаляю и создаю документ заново", name
            )
            await self.delete_document(doc_id)
        return await self.add_document_text(name, text, process_rule)

    async def delete_documents_by_prefix(self, prefix: str) -> int:
        """Удаляет все документы базы знаний, имя которых начинается с prefix."""
        if not self.kb_ready():
            return 0
        documents = await self.list_all_documents(keyword=prefix)
        removed = 0
        for d in documents:
            if (d.get("name") or "").startswith(prefix) and d.get("id"):
                await self.delete_document(d["id"])
                removed += 1
        return removed

    async def add_document_file(
        self, filename: str, content: bytes, process_rule: dict | None = None
    ) -> dict:
        """Загружает файл документом в базу знаний (create-by-file).

        Поддерживаются форматы, которые принимает Dify (txt, md, pdf, docx, csv,
        xlsx и т. п.). Файл и параметры индексации передаются multipart-запросом.
        """
        if not self.kb_ready():
            log.warning("Dataset не настроен — пропускаю загрузку файла в базу знаний")
            return {"skipped": True}

        url = f"{self.base_url}/datasets/{self.dataset_id}/document/create-by-file"
        headers = {"Authorization": f"Bearer {self.dataset_api_key}"}
        data = json.dumps({
            "indexing_technique": "high_quality",
            "process_rule": process_rule or DEFAULT_PROCESS_RULE,
        })
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            resp = await client.post(
                url, headers=headers,
                data={"data": data},
                files={"file": (filename, content)},
            )
            if resp.status_code >= 400:
                log.error("Dify add_document_file error %s: %s", resp.status_code, resp.text)
            try:
                return resp.json()
            except json.JSONDecodeError:
                return {"_status": resp.status_code, "_text": resp.text}


def _ask_escalation_default() -> dict:
    """Безопасный ответ `ask()` при любом сбое: вопрос уйдёт оператору, не потеряется.

    `found_in_kb=False` → бот эскалирует; `on_topic=True` → тематический фильтр не
    отсекает человека из-за недоступности Dify (см. MaxBot._handle_user_question)."""
    return {
        "answer": "", "found_in_kb": False, "confidence": 0.0,
        "on_topic": True, "topic_confidence": 0.0,
    }


def _configured(value: str) -> bool:
    text = (value or "").strip().lower()
    if not text:
        return False
    placeholders = ("xxxxxxxx", "change-me", "replace_with", "replace-with")
    return not any(marker in text for marker in placeholders)


def _parse_structured(raw: str) -> dict:
    text = (raw or "").strip()
    text = text.replace("```json", "").replace("```", "").strip()
    parsed = None
    try:
        parsed = json.loads(text)
    except Exception:
        start, end = text.find("{"), text.rfind("}")
        if start >= 0 and end > start:
            try:
                parsed = json.loads(text[start : end + 1])
            except Exception:
                parsed = None

    if not isinstance(parsed, dict):
        # Не смогли распарсить — отправляем на эскалацию (тему считаем релевантной,
        # чтобы не отсечь живого человека из-за сбоя парсинга).
        return {
            "answer": text, "found_in_kb": False, "confidence": 0.0,
            "on_topic": True, "topic_confidence": 0.0,
        }

    try:
        confidence = float(parsed.get("confidence", 0.0))
    except (TypeError, ValueError):
        confidence = 0.0
    try:
        topic_confidence = float(parsed.get("topic_confidence", 0.0))
    except (TypeError, ValueError):
        topic_confidence = 0.0

    return {
        "answer": str(parsed.get("answer", "")).strip(),
        "found_in_kb": bool(parsed.get("found_in_kb", False)),
        "confidence": confidence,
        # При отсутствии поля считаем запрос релевантным (обратная совместимость).
        "on_topic": bool(parsed.get("on_topic", True)),
        "topic_confidence": topic_confidence,
    }
