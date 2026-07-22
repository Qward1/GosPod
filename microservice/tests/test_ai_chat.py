"""Проверки «Чата с ИИ» и базы знаний УСВО без сетевых вызовов."""
from __future__ import annotations

import asyncio
import os
import sys

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.max.dify_client import DifyApiError  # noqa: E402
from app.max.store import Store  # noqa: E402
from app.auth.deps import require_admin, require_user  # noqa: E402
from app.web.ai_chat import (  # noqa: E402
    AiChatProviderError,
    AiChatService,
    AiChatValidation,
    build_reference_docx,
    linkify_usvo_names,
)
from app.web.ai_chat_router import router as ai_chat_router  # noqa: E402
from app.web.usvo import record_from_fields  # noqa: E402
from app.web.usvo_knowledge import (  # noqa: E402
    META_DOC_PREFIX,
    UsvoCardKnowledgeSyncService,
    UsvoCardsMetaService,
    build_card_chunk,
)


def _record(rec_id=101, name="Иванов Иван Иванович"):
    return record_from_fields(rec_id, [
        ("ФИО УСВО", name),
        ("Дата рождения", "14.07.1987"),
        ("Статус", "ветеран боевых действий"),
        ("Телефон", "+7 900 000-00-01"),
        ("Адрес регистрации", "Московская область, г. Видное, ул. Школьная, д. 1"),
        ("Инвалидность", "да, 3 группа"),
        ("Пол участника", "мужской"),
        ("В каких мерах поддержки нуждается", "трудоустройство"),
        ("Ветеран боевых действий", "да"),
    ])


class FakeDify:
    def __init__(self, answer="Иванов Иван Иванович нуждается в поддержке."):
        self.answer = answer
        self.docs: dict[str, str] = {}
        self.calls: list[dict] = []
        self.add_document_calls = 0

    def app_ready(self):
        return True

    def kb_ready(self):
        return True

    async def ask_text(self, query, user_key, inputs=None):
        self.calls.append({"query": query, "user": user_key})
        return {"answer": self.answer, "message_id": "m1", "conversation_id": "c1"}

    async def add_document_text(self, name, text, **_kwargs):
        self.add_document_calls += 1
        self.docs[name] = text
        return {"document": {"id": name}}

    async def upsert_document_text(self, name, text, match_prefix=None, **_kwargs):
        if match_prefix:
            for old in [key for key in self.docs if key.startswith(match_prefix) and key != name]:
                del self.docs[old]
        self.docs[name] = text
        return {"document": {"id": name}}

    async def delete_documents_by_prefix(self, prefix):
        keys = [key for key in self.docs if key.startswith(prefix)]
        for key in keys:
            del self.docs[key]
        return len(keys)


class FailingDify(FakeDify):
    async def ask_text(self, query, user_key, inputs=None):
        raise DifyApiError("Dify недоступен")


def _services(tmp_path, dify=None, records=None):
    store = Store(str(tmp_path / "state.db"))
    records = records or [_record()]
    provider = lambda: records
    meta = UsvoCardsMetaService(provider, lambda: 1_750_000_000)
    dify = dify or FakeDify()
    knowledge = UsvoCardKnowledgeSyncService(store, dify, provider, meta)
    chat = AiChatService(store, dify, provider, meta, knowledge)
    return store, meta, knowledge, chat, dify


def test_chat_crud_messages_and_user_isolation(tmp_path):
    store, _, _, chat, _ = _services(tmp_path)
    first = chat.create_chat("user-1", answer_type="brief_reference")
    second = chat.create_chat("user-1")
    foreign = chat.create_chat("user-2")

    assert {item["id"] for item in chat.list_chats("user-1")} == {first["id"], second["id"]}
    assert {item["id"] for item in chat.list_chats("user-2")} == {foreign["id"]}
    assert first["answerType"] == "brief_reference"

    store.add_ai_chat_message(first["id"], "user-1", "user", "Сколько карточек?")
    assert len(chat.list_messages("user-1", first["id"])) == 1
    assert store.list_ai_chat_messages(first["id"], "user-2") is None

    updated = chat.update_chat("user-1", first["id"], answer_type="detailed_reference")
    assert updated["answerType"] == "detailed_reference"
    chat.delete_chat("user-1", second["id"])
    assert [item["id"] for item in chat.list_chats("user-1")] == [first["id"]]


def test_card_chunk_contains_real_fields_and_link():
    chunk = build_card_chunk(_record(123))
    assert "ФИО: Иванов Иван Иванович" in chunk
    assert "ID карточки: 123" in chunk
    assert "Инвалидность: да, 3 группа" in chunk
    assert "Ссылка на карточку: /usvo/cards/123" in chunk
    assert "\n\nВсе поля карточки:" in chunk


def test_meta_contains_aggregates(tmp_path):
    _, meta, _, _, _ = _services(tmp_path)
    data = meta.build()
    text = meta.text()
    assert data["total"] == 1
    assert data["disability"]["с инвалидностью"] == 1
    assert data["gender"]["мужчины"] == 1
    assert data["flags"]["ветераны боевых действий"] == 1
    assert "Общее количество карточек: 1" in text
    assert "трудоустройство: 1" in text


def test_linkify_exact_name_and_preserve_existing_link():
    record = _record(555)
    linked = linkify_usvo_names("Данные: Иванов Иван Иванович.", [record])
    assert "[Иванов Иван Иванович](/usvo/cards/555)" in linked

    existing = "[Иванов Иван Иванович](/usvo/cards/555) уже указан."
    assert linkify_usvo_names(existing, [record]) == existing

    duplicate = _record(556)
    assert linkify_usvo_names("Иванов Иван Иванович", [record, duplicate]) == "Иванов Иван Иванович"


def test_knowledge_rebuild_is_idempotent(tmp_path):
    _, _, knowledge, _, dify = _services(tmp_path)
    first = asyncio.run(knowledge.rebuild())
    second = asyncio.run(knowledge.rebuild())
    assert first == {"ok": True, "count": 1}
    assert second == {"ok": True, "count": 1}
    assert len([name for name in dify.docs if name.startswith("Карточка УСВО #")]) == 1
    assert META_DOC_PREFIX in dify.docs


class FakeUsvoTableStore:
    """Табличный источник карточек (аналог UsvoStore) для теста delete_usvo."""

    def __init__(self, records):
        self._records = list(records)

    def all(self):
        return list(self._records)

    def get(self, rec_id):
        return next((r for r in self._records if r.id == rec_id), None)


def test_delete_usvo_reveals_table_card_without_full_rebuild(tmp_path):
    """Удаление оверрайда точечно правит базу знаний, а не пересобирает её целиком.

    Регрессия: раньше delete_usvo всегда делал knowledge.rebuild() — удалял и заново
    заливал ВСЕ документы датасета. Правка табличной карточки #1 создаёт оверрайд в БД
    (табличная версия скрывается и её документ убирается из KB); после удаления
    оверрайда табличная карточка #1 снова видна и должна получить документ обратно —
    но точечно (upsert), без единого вызова add_document_text (это и есть признак
    полной пересборки датасета)."""
    from app.config import Config
    from app.max.client import MaxClient
    from app.web.service import WebService
    from app.web.usvo_db import USVO_DB_ID_BASE

    store = Store(str(tmp_path / "state.db"))
    usvo_table = FakeUsvoTableStore([_record(1)])
    cfg = Config()
    web = WebService(cfg, store, usvo_table, FakeDify(), MaxClient(cfg.max.api_base_url, ""), None)

    kb_dify = FakeDify()
    meta = UsvoCardsMetaService(web.all_usvo_records, lambda: 1_750_000_000)
    knowledge = UsvoCardKnowledgeSyncService(store, kb_dify, web.all_usvo_records, meta)
    web.attach_usvo_knowledge(knowledge)

    fields = [
        {"label": "ФИО УСВО", "value": "Иванов Иван Иванович"},
        {"label": "Дата рождения", "value": "14.07.1987"},
        {"label": "Телефон", "value": "+7 900 000-00-02"},
        {"label": "Статус", "value": "ветеран боевых действий"},
    ]
    edited = asyncio.run(web.update_usvo(1, fields))
    assert edited["ok"] is True
    override_id = edited["id"]
    assert override_id == USVO_DB_ID_BASE + 1

    card_docs = {k for k in kb_dify.docs if k.startswith("Карточка УСВО #")}
    assert card_docs == {f"Карточка УСВО #{override_id}: Иванов Иван Иванович"}

    kb_dify.add_document_calls = 0

    deleted = asyncio.run(web.delete_usvo(override_id))
    assert deleted["ok"] is True
    assert deleted["knowledge_sync"]["ok"] is True

    card_docs_after = {k for k in kb_dify.docs if k.startswith("Карточка УСВО #")}
    assert card_docs_after == {"Карточка УСВО #1: Иванов Иван Иванович"}
    # Полной пересборки не было — ни одного add_document_text (только точечный upsert).
    assert kb_dify.add_document_calls == 0


def test_send_message_saves_history_type_and_links(tmp_path):
    _, _, _, chat, dify = _services(tmp_path)
    created = chat.create_chat("user-1", answer_type="brief_reference")
    result = asyncio.run(chat.send_message(
        "user-1", created["id"], "Кто нуждается в поддержке?"
    ))
    messages = chat.list_messages("user-1", created["id"])
    assert [message["role"] for message in messages] == ["user", "assistant"]
    assert result["message"]["metadata"]["answerType"] == "brief_reference"
    assert "[Иванов Иван Иванович](/usvo/cards/101)" in result["message"]["content"]
    assert "Краткая справка" in dify.calls[0]["query"]
    assert chat.get_chat("user-1", created["id"])["title"].startswith("Кто нуждается")


def test_reference_message_produces_docx(tmp_path):
    _, _, _, chat, _ = _services(tmp_path)
    created = chat.create_chat("user-1", answer_type="brief_reference")
    result = asyncio.run(chat.send_message(
        "user-1", created["id"], "Кто нуждается в поддержке?"
    ))
    assert result["message"]["metadata"]["hasDocx"] is True
    data, filename = chat.message_docx("user-1", created["id"], result["message"]["id"])
    assert data[:2] == b"PK"  # zip-сигнатура .docx
    assert filename.endswith(".docx")


def test_text_message_has_no_docx(tmp_path):
    _, _, _, chat, _ = _services(tmp_path)
    created = chat.create_chat("user-1", answer_type="text")
    result = asyncio.run(chat.send_message("user-1", created["id"], "Сколько карточек?"))
    assert result["message"]["metadata"]["hasDocx"] is False
    with pytest.raises(AiChatValidation):
        chat.message_docx("user-1", created["id"], result["message"]["id"])


def test_build_reference_docx_strips_links():
    docx = build_reference_docx(
        "Краткая справка\n\nОтвет:\n[Иванов](/usvo/cards/1) нуждается.", "brief_reference"
    )
    assert docx[:2] == b"PK"


def test_dify_error_is_reported_and_user_message_remains(tmp_path):
    _, _, _, chat, _ = _services(tmp_path, dify=FailingDify())
    created = chat.create_chat("user-1")
    with pytest.raises(AiChatProviderError, match="Dify недоступен"):
        asyncio.run(chat.send_message("user-1", created["id"], "Тестовый вопрос"))
    messages = chat.list_messages("user-1", created["id"])
    assert [message["role"] for message in messages] == ["user"]


def test_ai_chat_http_api_uses_authenticated_owner(tmp_path):
    _, _, _, chat, _ = _services(tmp_path)
    current = {"sub": "user-1", "name": "Первый", "role": "Сотрудник"}
    app = FastAPI()
    app.include_router(ai_chat_router)
    app.state.ai_chat = chat
    app.dependency_overrides[require_user] = lambda: dict(current)
    app.dependency_overrides[require_admin] = lambda: dict(current)

    with TestClient(app) as client:
        created = client.post("/api/web/ai-chats", json={"answerType": "text"})
        assert created.status_code == 200
        chat_id = created.json()["chat"]["id"]
        assert client.get("/api/web/ai-chats").json()["items"][0]["id"] == chat_id

        current["sub"] = "user-2"
        assert client.get("/api/web/ai-chats").json()["items"] == []
        assert client.get(f"/api/web/ai-chats/{chat_id}").status_code == 404
