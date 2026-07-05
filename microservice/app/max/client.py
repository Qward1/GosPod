"""Тонкая обёртка над MAX Bot API (platform-api.max.ru).

Актуальная модель API (см. https://dev.max.ru/docs-api):
  - база: https://platform-api.max.ru;
  - авторизация заголовком  Authorization: <token>  (НЕ query-параметром);
  - отправка сообщения: POST /messages?chat_id=<id>, тело {text, attachments}
    (получатель — query-параметр, иначе 400 "Unknown recipient");
  - inline-клавиатура: attachment {"type": "inline_keyboard", "payload": {"buttons": [[...]]}};
  - ответ на нажатие кнопки: POST /answers?callback_id=<id>, тело {notification|message};
  - подписка вебхука: POST /subscriptions, тело {url};
  - long polling: GET /updates?marker=&timeout= (для локальной отладки);
  - инфо о боте: GET /me.

Если в вашем инстансе MAX поля/пути отличаются — все различия локализованы здесь.
"""
from __future__ import annotations

import asyncio
import json
import logging

import httpx

log = logging.getLogger("max.client")


class MaxClient:
    def __init__(self, base_url: str, token: str, timeout: float = 15.0):
        self.base_url = base_url.rstrip("/")
        self.token = token
        self.timeout = timeout

    def _headers(self) -> dict:
        return {"Authorization": self.token, "Content-Type": "application/json"}

    @staticmethod
    def inline_keyboard(rows: list[list[dict]]) -> dict:
        """rows — список рядов кнопок MAX.

        Кнопка по умолчанию — callback: {"text": str, "payload": str}. Тип можно
        переопределить полем "type" (MAX поддерживает callback | link |
        request_contact | request_geo_location | chat). Так, кнопка «поделиться
        номером телефона» — {"type": "request_contact", "text": "..."} (без payload),
        а ссылка — {"type": "link", "text": "...", "url": "..."}.
        """
        buttons: list[list[dict]] = []
        for row in rows:
            brow: list[dict] = []
            for b in row:
                btn: dict = {"type": b.get("type", "callback"), "text": b["text"]}
                if b.get("payload") is not None:
                    btn["payload"] = b["payload"]
                if b.get("url") is not None:
                    btn["url"] = b["url"]
                brow.append(btn)
            buttons.append(brow)
        return {"type": "inline_keyboard", "payload": {"buttons": buttons}}

    async def send_message(
        self,
        text: str,
        chat_id: str | int | None = None,
        user_id: str | int | None = None,
        keyboard_rows: list[list[dict]] | None = None,
    ) -> dict:
        """Отправляет сообщение в чат (chat_id) или пользователю (user_id).

        Получатель (chat_id/user_id) передаётся query-параметром — этого требует MAX
        (иначе ответ 400 "Unknown recipient").
        """
        body: dict = {"text": text}
        if keyboard_rows:
            body["attachments"] = [self.inline_keyboard(keyboard_rows)]

        url = f"{self.base_url}/messages"
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            # Сначала пробуем chat_id (групповой чат / диалог с известным chat_id).
            if chat_id is not None:
                resp = await client.post(
                    url, headers=self._headers(),
                    params={"chat_id": _maybe_int(chat_id)}, json=body,
                )
                # В личном диалоге chat_id часто отсутствует и подменяется user_id —
                # тогда MAX отвечает 400 (нет чата с таким id). Повторяем по user_id.
                if resp.status_code >= 400 and user_id is not None and str(user_id) != "":
                    log.warning("MAX send by chat_id=%s failed %s, retry by user_id=%s",
                                chat_id, resp.status_code, user_id)
                    resp = await client.post(
                        url, headers=self._headers(),
                        params={"user_id": _maybe_int(user_id)}, json=body,
                    )
            elif user_id is not None:
                resp = await client.post(
                    url, headers=self._headers(),
                    params={"user_id": _maybe_int(user_id)}, json=body,
                )
            else:
                log.error("MAX send_message: не указан ни chat_id, ни user_id")
                return {"ok": False}

            if resp.status_code >= 400:
                log.error("MAX send_message error %s: %s", resp.status_code, resp.text)
            return _safe_json(resp)

    async def send_document(
        self, file_bytes: bytes, filename: str, chat_id: str | int,
        caption: str = "", keyboard_rows: list[list[dict]] | None = None,
        user_id: str | int | None = None,
    ) -> dict:
        """Загружает файл в MAX и отправляет его получателю (best-effort).

        MAX-файл отправляется в два шага: POST /uploads?type=file → URL загрузки,
        затем PUT/POST байтов туда → token, который кладётся в attachment сообщения.
        Возвращает {"ok": bool, ...}. При ошибке не бросает — вызывающий код
        деградирует до текстового превью.
        """
        try:
            async with httpx.AsyncClient(timeout=self.timeout + 30) as client:
                up = await client.post(
                    f"{self.base_url}/uploads",
                    headers={"Authorization": self.token},
                    params={"type": "file"},
                )
                if up.status_code >= 400:
                    log.warning("MAX upload url error %s: %s", up.status_code, up.text[:200])
                    return {"ok": False}
                upload_url = (up.json() or {}).get("url")
                if not upload_url:
                    return {"ok": False}
                put = await client.post(
                    upload_url, files={"data": (filename, file_bytes,
                    "application/vnd.openxmlformats-officedocument.wordprocessingml.document")},
                )
                if put.status_code >= 400:
                    log.warning("MAX upload put error %s: %s", put.status_code, put.text[:200])
                    return {"ok": False}
                token = (put.json() or {}).get("token")
                if not token:
                    return {"ok": False}

                body: dict = {"text": caption or filename,
                              "attachments": [{"type": "file", "payload": {"token": token}}]}
                if keyboard_rows:
                    body["attachments"].append(self.inline_keyboard(keyboard_rows))
                msg_url = f"{self.base_url}/messages"

                async def _send_once() -> httpx.Response:
                    # Сначала по chat_id; в личном диалоге chat_id может отсутствовать —
                    # тогда повтор по user_id.
                    r = await client.post(
                        msg_url, headers=self._headers(),
                        params={"chat_id": _maybe_int(chat_id)}, json=body,
                    )
                    if r.status_code >= 400 and user_id is not None and str(user_id) != "":
                        r = await client.post(
                            msg_url, headers=self._headers(),
                            params={"user_id": _maybe_int(user_id)}, json=body,
                        )
                    return r

                # Загруженный файл MAX обрабатывает асинхронно: первые попытки могут
                # вернуть attachment.not.ready / file.not.processed, пока токен «не готов».
                # Повторяем отправку с паузами, пока файл не обработается.
                resp = None
                for attempt in range(6):
                    resp = await _send_once()
                    if resp.status_code < 400:
                        out = _safe_json(resp)
                        out["ok"] = True
                        return out
                    if "not.ready" not in resp.text and "not_processed" not in resp.text \
                            and "not.processed" not in resp.text:
                        break  # ошибка не про обработку файла — повторять бессмысленно
                    await asyncio.sleep(1.2)

                log.warning(
                    "MAX send file error %s: %s",
                    resp.status_code if resp is not None else "?",
                    resp.text[:200] if resp is not None else "",
                )
                return {"ok": False}
        except Exception as exc:  # noqa: BLE001
            log.warning("MAX send_document failed: %s", exc)
            return {"ok": False}

    async def delete_message(self, message_id: str) -> dict:
        """Удаляет сообщение по его id (mid). Используется для исчезновения оффера."""
        url = f"{self.base_url}/messages"
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            resp = await client.delete(
                url, headers=self._headers(), params={"message_id": message_id}
            )
            if resp.status_code >= 400:
                log.warning("MAX delete_message error %s: %s", resp.status_code, resp.text)
            return _safe_json(resp)

    async def answer_callback(self, callback_id: str, notification: str | None = None) -> dict:
        """Подтверждает нажатие inline-кнопки.

        MAX требует в теле `message` или `notification` — пустой body даёт 400
        (proto.payload). Если показывать всплывашку нечего (тихий ack), POST не шлём:
        во всех таких сценариях исходное сообщение с кнопкой тут же удаляется, что и
        снимает «крутилку» на кнопке.
        """
        if not notification:
            return {"ok": True, "skipped": True}
        url = f"{self.base_url}/answers"
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            resp = await client.post(
                url, headers=self._headers(),
                params={"callback_id": callback_id}, json={"notification": notification},
            )
            if resp.status_code >= 400:
                log.error("MAX answer_callback error %s: %s", resp.status_code, resp.text)
            return _safe_json(resp)

    # ---- админ/диагностика -------------------------------------------------

    async def get_me(self) -> dict:
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            resp = await client.get(f"{self.base_url}/me", headers=self._headers())
            return _safe_json(resp)

    async def subscribe(self, webhook_url: str) -> dict:
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            resp = await client.post(
                f"{self.base_url}/subscriptions", headers=self._headers(), json={"url": webhook_url}
            )
            return _safe_json(resp)

    async def get_subscriptions(self) -> dict:
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            resp = await client.get(f"{self.base_url}/subscriptions", headers=self._headers())
            return _safe_json(resp)

    async def unsubscribe(self, webhook_url: str) -> dict:
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            resp = await client.delete(
                f"{self.base_url}/subscriptions",
                headers=self._headers(),
                params={"url": webhook_url},
            )
            return _safe_json(resp)

    async def get_updates(self, marker: int | None = None, timeout: int = 30) -> dict:
        """Long polling — для локальной отладки без публичного HTTPS-вебхука."""
        params: dict = {"timeout": timeout}
        if marker is not None:
            params["marker"] = marker
        async with httpx.AsyncClient(timeout=self.timeout + timeout) as client:
            resp = await client.get(f"{self.base_url}/updates", headers=self._headers(), params=params)
            return _safe_json(resp)


def _maybe_int(value):
    s = str(value)
    if s.lstrip("-").isdigit():
        try:
            return int(s)
        except ValueError:
            return value
    return value


def _safe_json(resp: httpx.Response) -> dict:
    try:
        return resp.json()
    except json.JSONDecodeError:
        return {"_status": resp.status_code, "_text": resp.text}
