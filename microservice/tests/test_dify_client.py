"""Устойчивость DifyClient.ask() к сбоям сети/HTTP/JSON (без реальной сети).

При любом сбое ask() обязан вернуть безопасный дефолт, при котором бот эскалирует
вопрос оператору (found_in_kb=False), а тематический фильтр не отсекает человека
(on_topic=True). Иначе сбой Dify молча «съедал» бы вопрос гражданина.

Запуск из каталога microservice:
    python -m pytest tests/test_dify_client.py -q
"""
from __future__ import annotations

import asyncio
import os
import sys

import httpx

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.max.dify_client import DifyClient  # noqa: E402


def _client() -> DifyClient:
    return DifyClient(
        base_url="https://dify.example/v1",
        app_key="real-app-key",  # не плейсхолдер → ask() пойдёт в сеть
        dataset_api_key="",
        dataset_id="",
    )


def _assert_escalation_default(result: dict) -> None:
    assert result["found_in_kb"] is False
    assert result["confidence"] == 0.0
    assert result["on_topic"] is True
    assert result["answer"] == ""


class _RaisingClient:
    """Заглушка httpx.AsyncClient, чей post() бросает переданное исключение."""

    def __init__(self, exc: Exception):
        self._exc = exc

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def post(self, *args, **kwargs):
        raise self._exc


class _StatusClient:
    def __init__(self, status: int, text: str = "", body=None):
        self._resp = httpx.Response(status, json=body) if body is not None \
            else httpx.Response(status, text=text)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def post(self, *args, **kwargs):
        return self._resp


def test_ask_network_error_escalates(monkeypatch):
    monkeypatch.setattr(
        httpx, "AsyncClient",
        lambda *a, **k: _RaisingClient(httpx.ConnectTimeout("boom")),
    )
    _assert_escalation_default(asyncio.run(_client().ask("вопрос", "max-1")))


def test_ask_http_error_escalates(monkeypatch):
    monkeypatch.setattr(
        httpx, "AsyncClient",
        lambda *a, **k: _StatusClient(500, text="server error"),
    )
    _assert_escalation_default(asyncio.run(_client().ask("вопрос", "max-1")))


def test_ask_invalid_json_escalates(monkeypatch):
    monkeypatch.setattr(
        httpx, "AsyncClient",
        lambda *a, **k: _StatusClient(200, text="<html>not json</html>"),
    )
    _assert_escalation_default(asyncio.run(_client().ask("вопрос", "max-1")))


def test_ask_unconfigured_key_escalates():
    client = DifyClient("https://dify.example/v1", "CHANGE-ME", "", "")
    _assert_escalation_default(asyncio.run(client.ask("вопрос", "max-1")))


def test_ask_parses_structured_answer(monkeypatch):
    payload = {
        "answer": '{"answer":"Ответ из БЗ","found_in_kb":true,"confidence":0.9,'
                  '"on_topic":true,"topic_confidence":0.95}'
    }
    monkeypatch.setattr(
        httpx, "AsyncClient",
        lambda *a, **k: _StatusClient(200, body=payload),
    )
    result = asyncio.run(_client().ask("вопрос", "max-1"))
    assert result["found_in_kb"] is True
    assert result["confidence"] == 0.9
    assert result["answer"] == "Ответ из БЗ"
