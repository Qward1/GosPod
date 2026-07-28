"""ИИ-обработка документов: анализ фото (визуальная модель) и заполнение заявления."""
from __future__ import annotations

import json
import logging
import os
import re

import httpx

from app.config import Config
from app.docs.forms import DEFAULT_MEASURE, normalize_application
from app.docs.measures import match_measure_offline
from app.docs.templates import fill_template_docx

log = logging.getLogger("docs.ai")

_DOCX_MIME = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"

# промт для прямого вызова vision-модели; у Dify-провайдера промт внутри ассистента
_VISION_SYSTEM_PROMPT = """\
Ты — ассистент контакт-центра поддержки участников СВО Ленинского городского округа \
Московской области. На вход поступают ФОТОГРАФИИ документов заявителя: паспорт РФ, \
удостоверение «Ветеран боевых действий», квитанция/ЕПД с лицевыми счетами ЖКУ, \
свидетельства о рождении детей, реквизиты банковского счёта.

Задачи:
1. Распознай документы и извлеки данные заявителя.
2. Определи доступную меру социальной поддержки. Если есть удостоверение ВБД или \
подтверждение участия в СВО — основная мера: «Ежемесячная денежная компенсация \
расходов на оплату ЖКУ» (категория «Ветеран боевых действий (участник СВО)», код 061).
3. Верни СТРОГО JSON по схеме (без текста до/после, без ```):

{
  "measure_key": "zhku_compensation",
  "measure_title": "Ежемесячная денежная компенсация расходов на оплату ЖКУ",
  "category": "Ветеран боевых действий (участник СВО)",
  "category_code": "061",
  "department": "Министерство социального развития Московской области, Ленинское управление социальной защиты населения",
  "applicant": {
    "fio": "Фамилия Имя Отчество",
    "birth_date": "ДД.ММ.ГГГГ",
    "passport_series": "0000",
    "passport_number": "000000",
    "passport_issued": "кем и когда выдан",
    "address": "адрес регистрации",
    "phone": "+7 ...",
    "email": ""
  },
  "ownership": "частное | муниципальное | государственное",
  "rooms": "число комнат, если видно",
  "family": [{"fio": "...", "birth_date": "ДД.ММ.ГГГГ", "relation": "супруга|сын|дочь|..."}],
  "providers": [{"name": "поставщик ЖКУ", "account": "номер лицевого счёта"}],
  "payment": {"method": "bank|post", "bank": "название банка", "account": "номер счёта"},
  "missing": ["чего не хватает для оформления, по-русски"],
  "confidence": 0.0,
  "summary_note": "1–2 предложения для гражданина: что распознано и на что он имеет право"
}

Правила:
- Не выдумывай данные. Чего не видно на фото — оставь пустой строкой "" и добавь пункт в "missing".
- Серию и номер паспорта верни цифрами, даты — в формате ДД.ММ.ГГГГ.
- confidence — твоя уверенность в распознавании (0..1).
- Никакого текста вне JSON.\
"""

_VISION_USER_TEXT = "Распознай приложенные фотографии документов и заполни JSON."

# промт для чтения документов обычным текстом — вход для подбора меры
_READ_TEXT_SYSTEM_PROMPT = """\
Ты — ассистент контакт-центра поддержки участников СВО Ленинского городского округа \
Московской области. На вход поступают ФОТОГРАФИИ документов заявителя.

Прочитай документы и кратко опиши их обычным текстом (НЕ JSON, без markdown): какие это \
документы, статус заявителя (например, ветеран боевых действий, участник СВО, член семьи \
участника СВО, вдова/родитель погибшего и т.п.) и любые факты, важные для подбора меры \
социальной поддержки: состав семьи и дети, жильё и оплата ЖКУ, инвалидность/ранение, \
потеря кормильца, трудоустройство и переобучение.

Правила:
- Описывай только то, что реально видно на фотографиях. Не выдумывай данные.
- 3–6 предложений связным текстом.\
"""

_READ_TEXT_USER = "Прочитай приложенные документы и опиши ситуацию заявителя текстом."

# офлайн-заглушка, чтобы демо-сценарий проходил без визуальной модели
_DEMO_DOCS_SUMMARY = (
    "Заявитель — ветеран боевых действий, участник специальной военной операции. "
    "Представлены паспорт гражданина РФ, удостоверение «Ветеран боевых действий» и "
    "квитанция на оплату жилищно-коммунальных услуг. В семье есть несовершеннолетние "
    "дети. Заявитель претендует на меры социальной поддержки, в том числе компенсацию "
    "расходов на оплату ЖКУ."
)


def _configured(value: str) -> bool:
    text = (value or "").strip().lower()
    if not text:
        return False
    return not any(m in text for m in ("xxxxxxxx", "change-me", "replace_with", "replace-with"))


def _extract_json(raw) -> dict | None:
    text = str(raw or "").strip().replace("```json", "").replace("```", "").strip()
    try:
        parsed = json.loads(text)
    except Exception:  # noqa: BLE001
        start, end = text.find("{"), text.rfind("}")
        if start >= 0 and end > start:
            try:
                parsed = json.loads(text[start:end + 1])
            except Exception:  # noqa: BLE001
                return None
        else:
            return None
    return parsed if isinstance(parsed, dict) else None


class DocAI:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.base_url = cfg.dify.base_url.rstrip("/")
        # Хост Dify без суффикса /v1 — для скачивания файлов по относительным ссылкам.
        self.host = re.sub(r"/v1/?$", "", self.base_url)
        self.timeout = 90.0
        # Кэш загруженного в Dify шаблона: (mtime, upload_file_id).
        self._tpl_cache: tuple[float, str] | None = None

    # ---- единый Dify-ассистент документов ----

    def _documents_key(self) -> str:
        """Ключ единого ассистента документов, если он настроен (иначе пусто)."""
        key = self.cfg.dify.documents_app_key
        return key if _configured(key) else ""

    async def _ask_documents_dify(
        self,
        key: str,
        user_key: str,
        task: str,
        *,
        fields: list[dict] | None = None,
        image_urls: list[str] | None = None,
        query: str = "",
    ) -> str:
        """Вызывает единый ассистент документов (роутер по `task`) и возвращает сырой ответ."""
        headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
        files = [
            {"type": "image", "transfer_method": "remote_url", "url": url}
            for url in (image_urls or []) if url
        ]
        inputs = {
            "task": task,
            "fields": json.dumps(fields, ensure_ascii=False) if fields is not None else "",
        }
        body = {
            "inputs": inputs,
            "query": query or task,
            "response_mode": "blocking",
            "user": user_key,
            "files": files,
        }
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            resp = await client.post(f"{self.base_url}/chat-messages", headers=headers, json=body)
            if resp.status_code >= 400:
                log.error("Documents Dify (%s) error %s: %s", task, resp.status_code, resp.text[:400])
                return ""
            data = resp.json()
        return str(data.get("answer", "") or "").strip()

    # ---- шаг 1: распознавание фотографий -----------------------------------

    async def analyze_documents(self, image_urls: list[str], user_key: str) -> dict:
        """Распознаёт документы по фото и предлагает меру поддержки."""
        vcfg = self.cfg.dify.vision
        docs_key = self._documents_key()
        try:
            if docs_key:
                raw = await self._ask_documents_dify(
                    docs_key, user_key, "recognize", image_urls=image_urls,
                    query="Распознай документы и предложи меру поддержки.",
                )
                data = _extract_json(raw)
                if data:
                    data["source"] = "dify"
                    return normalize_application(data)
            elif vcfg.provider == "openrouter":
                if _configured(vcfg.openrouter_api_key) and vcfg.openrouter_model.strip():
                    data = await self._ask_vision_openrouter(image_urls)
                    if data:
                        data["source"] = "openrouter"
                        return normalize_application(data)
                    log.warning("OpenRouter vision returned no data, using local fallback")
                else:
                    log.warning("OpenRouter vision not configured (missing api_key or model), using local fallback")
            else:
                key = self.cfg.dify.doc_vision_app_key
                if _configured(key):
                    data = await self._ask_vision_dify(image_urls, key, user_key)
                    if data:
                        data["source"] = "dify"
                        return normalize_application(data)
        except Exception as exc:  # noqa: BLE001
            log.warning("Vision recognize failed, using local fallback: %s", exc)

        result = _fallback_extract(len(image_urls))
        result["source"] = "local"
        return normalize_application(result)

    async def read_documents_text(self, image_urls: list[str], user_key: str) -> str:
        """Читает текст с фотографий документов и возвращает связное текстовое описание."""
        if not image_urls:
            return ""
        vcfg = self.cfg.dify.vision
        docs_key = self._documents_key()
        try:
            if docs_key:
                text = await self._ask_documents_dify(
                    docs_key, user_key, "read", image_urls=image_urls, query=_READ_TEXT_USER,
                )
                if text:
                    return text
            elif vcfg.provider == "openrouter":
                if _configured(vcfg.openrouter_api_key) and vcfg.openrouter_model.strip():
                    text = await self._read_text_openrouter(image_urls)
                    if text:
                        return text
                    log.warning("OpenRouter read_documents_text: пустой ответ, офлайн-фолбэк")
                else:
                    log.warning("OpenRouter не настроен для read_documents_text — офлайн-фолбэк")
            else:
                key = self.cfg.dify.doc_vision_app_key
                if _configured(key):
                    text = await self._read_text_dify(image_urls, key, user_key)
                    if text:
                        return text
        except Exception as exc:  # noqa: BLE001
            log.warning("read_documents_text failed, офлайн-фолбэк: %s", exc)
        return _DEMO_DOCS_SUMMARY

    async def _read_text_openrouter(self, image_urls: list[str]) -> str:
        """Vision-вызов, возвращающий сырой текст описания документов (без JSON-парсинга)."""
        vcfg = self.cfg.dify.vision
        content: list[dict] = [{"type": "text", "text": _READ_TEXT_USER}]
        for url in image_urls:
            if url:
                content.append({"type": "image_url", "image_url": {"url": url}})
        body = {
            "model": vcfg.openrouter_model,
            "messages": [
                {"role": "system", "content": _READ_TEXT_SYSTEM_PROMPT},
                {"role": "user", "content": content},
            ],
            "temperature": vcfg.temperature,
        }
        headers = {
            "Authorization": f"Bearer {vcfg.openrouter_api_key}",
            "Content-Type": "application/json",
        }
        base = vcfg.openrouter_base_url.rstrip("/")
        async with httpx.AsyncClient(timeout=vcfg.timeout) as client:
            resp = await client.post(f"{base}/chat/completions", headers=headers, json=body)
            if resp.status_code >= 400:
                log.error("read_documents_text OpenRouter error %s: %s",
                          resp.status_code, resp.text[:300])
                return ""
            data = resp.json()
        return str(data.get("choices", [{}])[0].get("message", {}).get("content", "") or "").strip()

    async def _read_text_dify(self, image_urls: list[str], key: str, user_key: str) -> str:
        """Vision-вызов Dify, возвращающий сырой текст ответа ассистента (без JSON-парсинга)."""
        headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
        files = [
            {"type": "image", "transfer_method": "remote_url", "url": url}
            for url in image_urls if url
        ]
        body = {
            "inputs": {},
            "query": _READ_TEXT_USER,
            "response_mode": "blocking",
            "user": user_key,
            "files": files,
        }
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            resp = await client.post(f"{self.base_url}/chat-messages", headers=headers, json=body)
            if resp.status_code >= 400:
                log.error("read_documents_text Dify error %s: %s", resp.status_code, resp.text[:300])
                return ""
            data = resp.json()
        return str(data.get("answer", "") or "").strip()

    async def _ask_vision_dify(
        self, image_urls: list[str], key: str, user_key: str, query: str | None = None
    ) -> dict | None:
        headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
        files = [
            {"type": "image", "transfer_method": "remote_url", "url": url}
            for url in image_urls if url
        ]
        body = {
            "inputs": {},
            "query": query or "Распознай документы и предложи меру поддержки.",
            "response_mode": "blocking",
            "user": user_key,
            "files": files,
        }
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            resp = await client.post(f"{self.base_url}/chat-messages", headers=headers, json=body)
            if resp.status_code >= 400:
                log.error("Vision Dify error %s: %s", resp.status_code, resp.text[:400])
                return None
            data = resp.json()
        return _extract_json(data.get("answer", ""))

    async def _ask_vision_openrouter(
        self, image_urls: list[str], system_prompt: str | None = None,
        user_text: str | None = None,
    ) -> dict | None:
        vcfg = self.cfg.dify.vision
        content: list[dict] = [{"type": "text", "text": user_text or _VISION_USER_TEXT}]
        for url in image_urls:
            if url:
                content.append({"type": "image_url", "image_url": {"url": url}})
        body = {
            "model": vcfg.openrouter_model,
            "messages": [
                {"role": "system", "content": system_prompt or _VISION_SYSTEM_PROMPT},
                {"role": "user", "content": content},
            ],
            "temperature": vcfg.temperature,
        }
        headers = {
            "Authorization": f"Bearer {vcfg.openrouter_api_key}",
            "Content-Type": "application/json",
        }
        base = vcfg.openrouter_base_url.rstrip("/")
        async with httpx.AsyncClient(timeout=vcfg.timeout) as client:
            resp = await client.post(f"{base}/chat/completions", headers=headers, json=body)
            if resp.status_code >= 400:
                log.error("Vision OpenRouter error %s: %s", resp.status_code, resp.text[:400])
                return None
            data = resp.json()
        raw = data.get("choices", [{}])[0].get("message", {}).get("content", "")
        return _extract_json(raw)

    # ---- шаг 2: заполнение заявления ---------------------------------------

    async def fill_application(self, extracted: dict, user_key: str) -> dict:
        """Раскладывает извлечённые данные по полям заявления (Dify или фолбэк)."""
        key = self.cfg.dify.doc_fill_app_key
        if _configured(key):
            try:
                data = await self._ask_fill(extracted, key, user_key)
                if data:
                    merged = normalize_application(data)
                    merged["source"] = "dify"
                    return merged
            except Exception as exc:  # noqa: BLE001
                log.warning("Doc-fill Dify failed, using local fallback: %s", exc)
        # Фолбэк: извлечённые данные уже нормализованы — используем как есть.
        out = normalize_application(extracted)
        out["source"] = extracted.get("source", "local")
        return out

    async def _ask_fill(self, extracted: dict, key: str, user_key: str) -> dict | None:
        headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
        body = {
            "inputs": {},
            "query": json.dumps(extracted, ensure_ascii=False),
            "response_mode": "blocking",
            "user": user_key,
        }
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            resp = await client.post(f"{self.base_url}/chat-messages", headers=headers, json=body)
            if resp.status_code >= 400:
                log.error("Doc-fill Dify error %s: %s", resp.status_code, resp.text[:400])
                return None
            data = resp.json()
        return _extract_json(data.get("answer", ""))

    # ---- сборка .docx в Dify-ассистенте (по DOCX-шаблону) ------------------

    async def generate_application_docx(self, data: dict, user_key: str) -> dict | None:
        """Формирует готовый .docx заявления в Dify-ассистенте заполнения."""
        key = self.cfg.dify.doc_fill_app_key
        if not _configured(key):
            return None
        try:
            headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
            inputs: dict = {}
            tpl = await self._upload_template(key, user_key)
            if tpl:
                inputs["template"] = tpl
            body = {
                "inputs": inputs,
                "query": json.dumps(data, ensure_ascii=False),
                "response_mode": "blocking",
                "user": user_key,
            }
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                resp = await client.post(f"{self.base_url}/chat-messages", headers=headers, json=body)
                if resp.status_code >= 400:
                    log.error("Doc-generate Dify error %s: %s", resp.status_code, resp.text[:400])
                    self._tpl_cache = None  # вдруг протух upload_file_id — перезальём в следующий раз
                    return None
                payload = resp.json()
                url = _find_docx_url(payload)
                if not url:
                    log.warning("Doc-generate: в ответе Dify нет файла .docx (ответ: %s)",
                                str(payload.get("answer", ""))[:200])
                    return None
                file_bytes = await self._download_file(client, url, key)
            if not file_bytes:
                return None
            log.info("Заявление .docx сформировано в Dify (%d байт).", len(file_bytes))
            return {"bytes": file_bytes, "source": "dify"}
        except Exception as exc:  # noqa: BLE001
            log.warning("Doc-generate Dify failed, использую офлайн-сборку: %s", exc)
            self._tpl_cache = None
            return None

    async def _upload_template(self, app_key: str, user_key: str) -> dict | None:
        """Загружает DOCX-шаблон в Dify (Files API) и возвращает ссылку-объект для inputs."""
        path = (self.cfg.dify.doc_template_path or "").strip()
        if not path or not os.path.exists(path):
            if path:
                log.warning("DOCX-шаблон не найден: %s — Dify возьмёт свой template.", path)
            return None
        mtime = os.path.getmtime(path)
        if self._tpl_cache and self._tpl_cache[0] == mtime:
            file_id = self._tpl_cache[1]
        else:
            with open(path, "rb") as f:
                content = f.read()
            files = {"file": (os.path.basename(path), content, _DOCX_MIME)}
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                resp = await client.post(
                    f"{self.base_url}/files/upload",
                    headers={"Authorization": f"Bearer {app_key}"},
                    data={"user": user_key},
                    files=files,
                )
            if resp.status_code >= 400:
                log.warning("Не удалось загрузить шаблон в Dify: %s %s",
                            resp.status_code, resp.text[:200])
                return None
            file_id = (resp.json() or {}).get("id")
            if not file_id:
                return None
            self._tpl_cache = (mtime, file_id)
        return {"type": "document", "transfer_method": "local_file", "upload_file_id": file_id}

    # ---- сценарий «Меры поддержки»: обобщённое распознавание/сборка ---------

    async def extract_fields(
        self, image_urls: list[str], fields: list[dict], user_key: str
    ) -> dict:
        """Извлекает значения для произвольного набора полей шаблона меры поддержки."""
        keys = [f["key"] for f in fields if f.get("key")]
        if not keys:
            return {"filled_fields": {}, "missing_fields": []}
        prompt = _build_fields_prompt(fields)
        vcfg = self.cfg.dify.vision
        docs_key = self._documents_key()
        try:
            if docs_key:
                raw = await self._ask_documents_dify(
                    docs_key, user_key, "extract", fields=fields, image_urls=image_urls,
                    query="Распознай документы и заполни JSON по указанным полям.",
                )
                data = _extract_json(raw)
                if data:
                    return _normalize_extract(data, keys)
                log.warning("Documents Dify extract_fields: ответ без JSON, офлайн-фолбэк")
            elif vcfg.provider == "openrouter":
                if _configured(vcfg.openrouter_api_key) and vcfg.openrouter_model.strip():
                    data = await self._ask_vision_openrouter(
                        image_urls, system_prompt=prompt,
                        user_text="Распознай документы и заполни JSON по указанным полям.",
                    )
                    if data:
                        return _normalize_extract(data, keys)
                    log.warning("OpenRouter extract_fields: пустой ответ, офлайн-фолбэк")
                else:
                    log.warning("OpenRouter не настроен для extract_fields — офлайн-фолбэк")
            else:
                key = self.cfg.dify.doc_vision_app_key
                if _configured(key):
                    data = await self._ask_vision_dify(image_urls, key, user_key, query=prompt)
                    if data:
                        return _normalize_extract(data, keys)
        except Exception as exc:  # noqa: BLE001
            log.warning("extract_fields failed, офлайн-фолбэк: %s", exc)

        return _fallback_fields(fields)

    async def translate_labels(self, fields: list[dict]) -> list[str]:
        """Переводит подписи незаполненных полей на понятный гражданину русский язык."""
        if not fields:
            return []
        base = [_offline_ru_label(f) for f in fields]
        vcfg = self.cfg.dify.vision
        # Поля, которые после офлайн-словаря всё ещё на латинице, — отдаём модели.
        to_translate = [
            f for f, lbl in zip(fields, base) if not re.search(r"[А-Яа-яЁё]", lbl)
        ]
        if not to_translate:
            return base
        docs_key = self._documents_key()
        try:
            if docs_key:
                raw = await self._ask_documents_dify(
                    docs_key, "documents-translate", "translate", fields=to_translate,
                    query="Переведи названия полей в короткие русские подписи.",
                )
                translated = _parse_label_map(raw, to_translate)
                if translated:
                    return [translated.get(f["key"]) or base[i] for i, f in enumerate(fields)]
            elif (
                vcfg.provider == "openrouter"
                and _configured(vcfg.openrouter_api_key)
                and vcfg.openrouter_model.strip()
            ):
                translated = await self._translate_openrouter(to_translate)
                if translated:
                    return [translated.get(f["key"]) or base[i] for i, f in enumerate(fields)]
        except Exception as exc:  # noqa: BLE001
            log.warning("translate_labels failed, офлайн-словарь: %s", exc)
        return base

    async def _translate_openrouter(self, fields: list[dict]) -> dict[str, str] | None:
        """Просит текстовую модель перевести названия полей в короткие русские подписи."""
        vcfg = self.cfg.dify.vision
        listing = "\n".join(
            f'- {f["key"]}: {f.get("label") or f["key"]}' for f in fields if f.get("key")
        )
        system = (
            "Ты переводишь технические названия полей анкеты на понятный гражданину "
            "русский язык. Для каждого поля дай короткую русскую подпись (1–4 слова, "
            "именительный падеж, без пояснений). Верни СТРОГО JSON без текста до/после и "
            'без ```: {"<ключ>": "<русская подпись>", ...}. Ключи используй ровно те, '
            "что в списке."
        )
        user = f"Поля (ключ: текущее название):\n{listing}"
        body = {
            "model": vcfg.openrouter_model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": 0.0,
        }
        headers = {
            "Authorization": f"Bearer {vcfg.openrouter_api_key}",
            "Content-Type": "application/json",
        }
        base = vcfg.openrouter_base_url.rstrip("/")
        async with httpx.AsyncClient(timeout=vcfg.timeout) as client:
            resp = await client.post(f"{base}/chat/completions", headers=headers, json=body)
            if resp.status_code >= 400:
                log.error("translate_labels OpenRouter error %s: %s",
                          resp.status_code, resp.text[:300])
                return None
            data = resp.json()
        raw = data.get("choices", [{}])[0].get("message", {}).get("content", "")
        parsed = _extract_json(raw)
        if not isinstance(parsed, dict):
            return None
        out: dict[str, str] = {}
        for f in fields:
            val = str(parsed.get(f["key"], "") or "").strip()
            if val:
                out[f["key"]] = val
        return out or None

    async def generate_measure_docx(
        self, template_path: str, values: dict, user_key: str
    ) -> dict | None:
        """Собирает .docx меры поддержки, подставляя значения в загруженный шаблон."""
        path = (template_path or "").strip()
        if not path or not os.path.exists(path):
            if path:
                log.warning("Шаблон меры не найден: %s", path)
            return None
        try:
            with open(path, "rb") as f:
                tpl_bytes = f.read()
            out = fill_template_docx(tpl_bytes, values)
            return {"bytes": out, "source": "local"}
        except Exception as exc:  # noqa: BLE001
            log.warning("generate_measure_docx failed: %s", exc)
            return None

    async def select_measure(
        self, query: str, measures: list[dict], user_key: str
    ) -> dict | None:
        """Подбирает меру поддержки по описанию ситуации гражданина."""
        key = self.cfg.dify.measure_app_key
        if _configured(key):
            try:
                headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
                body = {
                    "inputs": {}, "query": query, "response_mode": "blocking",
                    "user": user_key,
                }
                async with httpx.AsyncClient(timeout=self.timeout) as client:
                    resp = await client.post(
                        f"{self.base_url}/chat-messages", headers=headers, json=body
                    )
                    if resp.status_code < 400:
                        parsed = _extract_json(resp.json().get("answer", ""))
                        if isinstance(parsed, dict) and parsed.get("found"):
                            return {
                                "measure_id": parsed.get("measure_id"),
                                "measure_title": parsed.get("measure_title", ""),
                                "found": True,
                                "confidence": float(parsed.get("confidence", 0.0) or 0.0),
                            }
                    else:
                        log.error("select_measure Dify error %s: %s",
                                  resp.status_code, resp.text[:300])
            except Exception as exc:  # noqa: BLE001
                log.warning("select_measure Dify failed, офлайн-подбор: %s", exc)
        return match_measure_offline(query, measures)

    async def _download_file(self, client: httpx.AsyncClient, url: str, app_key: str) -> bytes | None:
        full = url if url.startswith("http") else self.host + ("" if url.startswith("/") else "/") + url
        # ссылки Dify обычно подписаны; на всякий случай пробуем и с Bearer
        for headers in ({}, {"Authorization": f"Bearer {app_key}"}):
            r = await client.get(full, headers=headers)
            if r.status_code < 400 and r.content:
                return r.content
        log.warning("Не удалось скачать сформированный .docx из Dify: %s", full)
        return None


def _clean_url(u: str) -> str:
    """Убирает хвостовую пунктуацию у ссылки."""
    return (u or "").strip().rstrip(").,;]>\"'")


def _find_docx_url(payload: dict) -> str | None:
    """Вытаскивает ссылку на сгенерированный .docx из ответа Dify (chat-messages)."""
    urls: list[str] = []
    for f in payload.get("message_files") or []:
        if isinstance(f, dict) and f.get("url"):
            urls.append(f["url"])
    # подстраховка: ищем ссылки и в тексте ответа, не захватывая хвост markdown-ссылки
    urls += re.findall(
        r'https?://[^\s"\'<>)\]]+|/files/[^\s"\'<>)\]]+',
        json.dumps(payload, ensure_ascii=False),
    )
    urls = [_clean_url(u) for u in urls if u]
    for u in urls:
        if ".docx" in u.lower():
            return u
    return urls[0] if urls else None


def _build_fields_prompt(fields: list[dict]) -> str:
    """Динамический промпт для распознавания произвольных полей шаблона меры."""
    lines = "\n".join(f"- {f['key']}: {f.get('label') or f['key']}" for f in fields if f.get("key"))
    return (
        "Ты — ассистент контакт-центра поддержки участников СВО Ленинского городского "
        "округа Московской области. На вход поступают ФОТОГРАФИИ документов заявителя.\n\n"
        "Извлеки значения СТРОГО для следующих полей (ключ: что это):\n"
        f"{lines}\n\n"
        "Верни СТРОГО JSON без текста до/после и без ```:\n"
        '{"filled_fields": {"<ключ>": "<значение>", ...}, "missing_fields": ["<ключ>", ...]}\n\n'
        "Правила:\n"
        "- Не выдумывай данные. Чего не видно на фото — клади ключ в missing_fields, "
        "а в filled_fields его не добавляй.\n"
        "- Даты — в формате ДД.ММ.ГГГГ, серию и номер документов — цифрами.\n"
        "- Ключи в ответе используй ровно те, что перечислены выше."
    )


def _parse_label_map(raw: str, fields: list[dict]) -> dict[str, str] | None:
    """Парсит ответ ветки translate ({"<key>": "<подпись>"}) в словарь по ключам полей."""
    parsed = _extract_json(raw)
    if not isinstance(parsed, dict):
        return None
    out: dict[str, str] = {}
    for f in fields:
        val = str(parsed.get(f["key"], "") or "").strip()
        if val:
            out[f["key"]] = val
    return out or None


def _normalize_extract(parsed: dict, keys: list[str]) -> dict:
    """Приводит ответ модели к {filled_fields, missing_fields} по запрошенным ключам."""
    filled_src = parsed.get("filled_fields")
    if not isinstance(filled_src, dict):
        # Модель могла вернуть плоский словарь {key: value} — используем его.
        filled_src = {k: v for k, v in parsed.items()
                      if k not in ("missing_fields", "filled_fields")}
    filled: dict[str, str] = {}
    missing: list[str] = []
    for k in keys:
        val = str(filled_src.get(k, "") or "").strip()
        if val:
            filled[k] = val
        else:
            missing.append(k)
    return {"filled_fields": filled, "missing_fields": missing}


# офлайн-словарь перевода полей; специфичные записи идут раньше общих,
# needle ищется и в ключе, и в подписи
_RU_FIELD_LABELS: list[tuple[tuple[str, ...], str]] = [
    (("passport_series", "pasport_seria", "series", "seria"), "Серия паспорта"),
    (("passport_number", "pasport_nomer"), "Номер паспорта"),
    (("passport_issued", "issued", "vydan"), "Кем и когда выдан паспорт"),
    (("passport", "pasport"), "Паспортные данные"),
    (("full_name", "fullname", "fio"), "ФИО (фамилия, имя, отчество)"),
    (("birth", "rozhd"), "Дата рождения"),
    (("snils",), "СНИЛС"),
    (("inn",), "ИНН"),
    (("military", "voenkom", "voen"), "Дата справки из военкомата"),
    # Земельный контроль / характеристики участка (частые англоязычные подписи).
    (("registry_number", "registry number", "registry", "reestr"), "Реестровый номер"),
    (("cadastral", "kadastr"), "Кадастровый номер"),
    (("permitted_use", "permitted use", "permitted", "razreshen"), "Вид разрешённого использования"),
    (("land_category", "land category", "land", "category", "kategor", "zeml"), "Категория земель"),
    (("coordinates", "coordinate", "koordinat"), "Координаты"),
    (("violation_type", "violation type", "narushen_tip", "tip_narush"), "Тип нарушения"),
    (("violation_phrase", "violation phrase", "violation_description",
      "violation", "narushen"), "Описание нарушения"),
    (("area_text", "area", "ploschad", "ploshad", "площад"), "Площадь"),
    # Общие адрес/контакты/реквизиты.
    (("registration_address", "registration", "propiska", "address", "adres"), "Адрес"),
    (("phone", "telefon"), "Номер телефона"),
    (("email", "mail", "pochta"), "Электронная почта"),
    (("bank_account", "account", "schet", "schyot", "rekviz"), "Номер банковского счёта"),
    (("bank_name", "bank"), "Название банка"),
    (("organization", "organiz"), "Организация"),
    (("description", "descr", "opisan"), "Описание"),
    (("title", "zagolov"), "Заголовок"),
    (("name", "naimenov"), "Наименование"),
    (("number", "nomer"), "Номер"),
    (("date", "data"), "Дата"),
]


def _offline_ru_label(field: dict) -> str:
    """Детерминированный русский label поля по его ключу/подписи (без сети)."""
    label = (field.get("label") or "").strip()
    # Подпись, уже заданную кириллицей (её ввёл администратор), оставляем как есть.
    if re.search(r"[А-Яа-яЁё]", label):
        return label
    # нижний регистр + разделители в пробелы, чтобы ловить оба написания
    haystack = re.sub(r"[_\-]+", " ", f"{field.get('key') or ''} {label}").lower()
    for needles, ru in _RU_FIELD_LABELS:
        if any(re.sub(r"[_\-]+", " ", n) in haystack for n in needles):
            return ru
    return label or field.get("key") or ""


# демо-значения распространённых полей для офлайн-сценария
_DEMO_FIELD_VALUES: list[tuple[tuple[str, ...], str]] = [
    (("full_name", "fio", "name"), "Смирнов Сергей Иванович"),
    (("birth",), "14.03.1988"),
    (("passport_series", "series"), "4614"),
    (("passport_number", "number"), "778512"),
    (("passport_issued", "issued"), "ГУ МВД России по Московской области, 25.06.2014"),
    (("snils",), "112-233-445 95"),
    (("inn",), "501203456789"),
    (("address", "registration"), "Московская область, г. Видное, ул. Школьная, д. 24, кв. 17"),
    (("phone",), "+7 916 200-31-44"),
    (("bank", "account", "rekviz", "schet"), "40817810000000012345"),
    (("military", "voenkom"), "Военный комиссариат Ленинского ГО, 12.02.2023"),
]


def _fallback_fields(fields: list[dict]) -> dict:
    """Офлайн-результат распознавания: демо-значения для узнаваемых ключей."""
    filled: dict[str, str] = {}
    missing: list[str] = []
    for f in fields:
        key = (f.get("key") or "").lower()
        value = ""
        for needles, demo in _DEMO_FIELD_VALUES:
            if any(n in key for n in needles):
                value = demo
                break
        if value:
            filled[f["key"]] = value
        else:
            missing.append(f["key"])
    return {"filled_fields": filled, "missing_fields": missing}


def _fallback_extract(n_photos: int) -> dict:
    """Детерминированный демо-результат распознавания (когда Dify не настроен)."""
    return {
        "measure_key": DEFAULT_MEASURE["key"],
        "measure_title": DEFAULT_MEASURE["title"],
        "category": DEFAULT_MEASURE["category"],
        "category_code": DEFAULT_MEASURE["category_code"],
        "department": DEFAULT_MEASURE["department"],
        "applicant": {
            "fio": "Смирнов Сергей Иванович",
            "birth_date": "14.03.1988",
            "passport_series": "46 14",
            "passport_number": "778512",
            "passport_issued": "ГУ МВД России по Московской области, 25.06.2014",
            "address": "Московская область, г. Видное, ул. Школьная, д. 24, кв. 17",
            "phone": "+7 916 200-31-44",
            "email": "",
        },
        "ownership": "частное",
        "rooms": "2",
        "family": [
            {"fio": "Смирнова Анна Петровна", "birth_date": "02.09.1990", "relation": "супруга"},
            {"fio": "Смирнов Иван Сергеевич", "birth_date": "11.05.2016", "relation": "сын"},
        ],
        "providers": [
            {"name": "МосОблЕИРЦ", "account": "5012 778 512"},
            {"name": "АО «Мособлгаз»", "account": "61-778-512"},
        ],
        "payment": {"method": "bank", "bank": "ПАО Сбербанк", "account": "40817810000000012345"},
        "missing": [] if n_photos >= 2 else ["квитанция ЕПД (лицевой счёт)"],
        "confidence": 0.82,
        "summary_note": (
            "Распознаны паспорт и удостоверение «Ветеран боевых действий». Заявитель "
            "имеет право на ежемесячную денежную компенсацию расходов на оплату ЖКУ."
        ),
    }
