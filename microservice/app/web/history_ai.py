"""Нормализация свободного текста «Истории взаимодействия» в события для UI.

Два пути (как и для остальных ИИ-функций кабинета):
  1) Dify-ассистент `history_assistant.yml` (ключ dify.history_app_key) — модель,
     подключённая к базе знаний data/history_styles.md, возвращает JSON `{events:[…]}`.
  2) Офлайн-нормализатор — если Dify не настроен/недоступен: разбивает текст на
     события и классифицирует их по тому же справочнику (history_styles.py).

Результат — список событий вида:
  {date, kind, status, style, title, detail, org}
— ровно то, что рисует renderTimeline на фронтенде.
"""
from __future__ import annotations

import json
import logging
import re

import httpx

from app.config import Config
from app.web import history_styles as hs

log = logging.getLogger("web.history_ai")

_DATE_RE = re.compile(r"\b(\d{1,2})[.\-/](\d{1,2})[.\-/](\d{2,4})\b")


def _configured(value: str) -> bool:
    text = (value or "").strip().lower()
    if not text:
        return False
    return not any(m in text for m in ("xxxxxxxx", "change-me", "replace_with", "replace-with"))


def history_dify_ready(cfg: Config) -> bool:
    return _configured(getattr(cfg.dify, "history_app_key", ""))


# ---------------------------------------------------------------------------
# Приведение «сырых» событий (из Dify или офлайн) к строгой схеме UI.
# ---------------------------------------------------------------------------

def coerce_events(raw_events: list) -> list[dict]:
    out: list[dict] = []
    for e in raw_events or []:
        if not isinstance(e, dict):
            continue
        title = str(e.get("title") or "").strip()
        detail = str(e.get("detail") or "").strip()
        if not title and not detail:
            continue
        status = hs.coerce_status(e.get("status", ""))
        kind = hs.coerce_kind(e.get("kind", ""))
        style = hs.coerce_style(e.get("style", ""), status)
        out.append({
            "date": str(e.get("date") or "").strip(),
            "kind": kind,
            "status": status,
            "style": style,
            "title": title or detail[:60],
            "detail": detail if detail and detail != title else "",
            "org": str(e.get("org") or "").strip(),
        })
    return out


# ---------------------------------------------------------------------------
# Офлайн-нормализатор (без LLM).
# ---------------------------------------------------------------------------

def _norm_date(seg: str) -> str:
    m = _DATE_RE.search(seg)
    if not m:
        return ""
    d, mth, y = m.group(1), m.group(2), m.group(3)
    if len(y) == 2:
        y = "20" + y
    try:
        return f"{int(d):02d}.{int(mth):02d}.{int(y):04d}"
    except ValueError:
        return ""


def _classify(seg: str) -> tuple[str, str, str]:
    """(kind, status, style) для одного сегмента истории."""
    low = seg.lower()

    kind = "info"
    for k, meta in hs.KINDS.items():
        if any(w in low for w in meta["keywords"]):
            kind = k
            break

    status = ""
    for st, words in hs.STATUS_KEYWORDS.items():
        if any(w in low for w in words):
            status = st
            break
    if not status:
        status = "выполнено"

    style = hs.STATUS_DEFAULT_STYLE.get(status, "info")
    if any(w in low for w in hs.NEGATIVE_KEYWORDS):
        style = "danger"
    return kind, status, style


# Однобуквенные/короткие сокращения, после которых точка не завершает предложение.
_ABBR = {"г", "д", "ул", "кв", "пос", "рп", "дер", "обл", "стр", "к", "корп",
         "им", "ст", "пер", "пр", "бул", "мкр", "зд", "руб", "тыс", "млн", "т", "п"}


def _split_sentences(s: str) -> list[str]:
    """Делит строку на предложения по «. » — но не по сокращениям и датам."""
    res: list[str] = []
    start = 0
    for m in re.finditer(r"\.\s+", s):
        i = m.start()
        j = i
        while j > 0 and s[j - 1].isalnum():
            j -= 1
        word = s[j:i].lower()
        nxt = s[m.end()] if m.end() < len(s) else ""
        if len(word) >= 2 and word not in _ABBR and (nxt.isupper() or nxt.isdigit()):
            res.append(s[start:i + 1])
            start = m.end()
    res.append(s[start:])
    return res


def _split_segments(text: str) -> list[str]:
    """Режет свободный текст на отдельные события (строки, разделители, предложения)."""
    text = (text or "").replace("\r", "\n").strip()
    if not text:
        return []
    parts: list[str] = []
    for chunk in re.split(r"[\n;•]+", text):
        for sentence in _split_sentences(chunk):
            sentence = sentence.strip(" \t•—-").strip()
            if sentence:
                parts.append(sentence)
    return parts or [text]


def _title_detail(seg: str) -> tuple[str, str]:
    # Уберём ведущую дату из заголовка.
    cleaned = _DATE_RE.sub("", seg).strip(" .,–—-:").strip()
    cleaned = cleaned[:1].upper() + cleaned[1:] if cleaned else seg
    words = cleaned.split()
    if len(words) <= 9:
        return cleaned, ""
    title = " ".join(words[:8]).rstrip(",.;") + "…"
    return title, cleaned


def local_normalize(text: str) -> list[dict]:
    events: list[dict] = []
    for seg in _split_segments(text):
        kind, status, style = _classify(seg)
        title, detail = _title_detail(seg)
        events.append({
            "date": _norm_date(seg),
            "kind": kind,
            "status": status,
            "style": style,
            "title": title,
            "detail": detail,
            "org": "",
        })
    return events


# ---------------------------------------------------------------------------
# Dify-путь.
# ---------------------------------------------------------------------------

def _extract_events(answer: str) -> list[dict]:
    text = (answer or "").strip().replace("```json", "").replace("```", "").strip()
    parsed = None
    try:
        parsed = json.loads(text)
    except Exception:  # noqa: BLE001
        start, end = text.find("{"), text.rfind("}")
        if start >= 0 and end > start:
            try:
                parsed = json.loads(text[start:end + 1])
            except Exception:  # noqa: BLE001
                parsed = None
    if isinstance(parsed, dict):
        return parsed.get("events") or parsed.get("history") or []
    if isinstance(parsed, list):
        return parsed
    return []


async def _ask_history_dify(cfg: Config, text: str, user_key: str) -> list[dict]:
    base_url = (cfg.dify.base_url or "").rstrip("/")
    app_key = getattr(cfg.dify, "history_app_key", "")
    url = f"{base_url}/chat-messages"
    headers = {"Authorization": f"Bearer {app_key}", "Content-Type": "application/json"}
    body = {
        "inputs": {"channel": "history"},
        "query": text,
        "response_mode": "blocking",
        "user": user_key,
    }
    timeout = getattr(cfg.dify.vision, "timeout", 90.0)
    async with httpx.AsyncClient(timeout=timeout) as client:
        resp = await client.post(url, headers=headers, json=body)
        if resp.status_code >= 400:
            log.warning("History Dify error %s: %s", resp.status_code, resp.text[:300])
            return []
        data = resp.json()
    return _extract_events(data.get("answer", ""))


async def normalize_history(cfg: Config, text: str, user_key: str = "history") -> list[dict]:
    """Главная точка входа: вернуть нормализованные события истории.

    При настроенном dify.history_app_key пробует LLM, иначе/при сбое — офлайн.
    """
    text = (text or "").strip()
    if not text:
        return []
    if history_dify_ready(cfg):
        try:
            events = coerce_events(await _ask_history_dify(cfg, text, user_key))
            if events:
                return events
        except Exception as exc:  # noqa: BLE001
            log.warning("History Dify failed, falling back to offline: %s", exc)
    return coerce_events(local_normalize(text))
