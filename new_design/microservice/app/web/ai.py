"""Черновики ответов и подсказки по мерам: Dify / OpenAI-compatible, офлайн-фолбэк."""
from __future__ import annotations

import json
import logging
import os
import re
from types import SimpleNamespace

import httpx

from app.config import Config
from app.max.dify_client import DifyClient
from app.web.usvo import UsvoRecord

log = logging.getLogger("web.ai")


def _web_ai_cfg(cfg: Config):
    """Возвращает настройки web.ai с fallback для старого app/config.py на сервере."""
    return getattr(cfg.web, "ai", SimpleNamespace(
        provider="local",
        dify_base_url="",
        dify_app_key="",
        dify_mode="auto",
        dify_input_variable="query",
        openai_base_url="",
        openai_api_key="",
        openai_model="",
        temperature=0.2,
        timeout=60.0,
    ))


def _configured(value: str) -> bool:
    text = (value or "").strip().lower()
    if not text:
        return False
    return not any(marker in text for marker in ("xxxxxxxx", "change-me", "replace_with", "replace-with"))


def dify_ready(cfg: Config) -> bool:
    """Оставлено для совместимости meta; по смыслу теперь это web AI ready."""
    ai = _web_ai_cfg(cfg)
    provider = (ai.provider or "local").strip().lower()
    if provider == "dify":
        return _configured(ai.dify_app_key)
    if provider == "openai_compatible":
        return bool((ai.openai_base_url or "").strip() and (ai.openai_model or "").strip())
    return False


def _greeting(name: str) -> str:
    return f"Уважаемый(ая) {name}!" if name else "Уважаемый гражданин!"


def _tokens(text: str) -> set[str]:
    """Грубая токенизация: слова от 5 букв, усечённые до 6 символов."""
    words = re.findall(r"[а-яёa-z]{5,}", (text or "").lower())
    return {w[:6] for w in words}


def _overlap(a: set[str], b: set[str]) -> float:
    """Доля токенов a, встречающихся в b (0..1)."""
    if not a:
        return 0.0
    return len(a & b) / len(a)


def _draft_confidence(
    *, source: str, usvo: UsvoRecord | None, question: str, kb_text: str, body: str
) -> float:
    """Детерминированная оценка «уверенности» чернового ответа (0..1)."""
    q = _tokens(question)
    q_in_answer = _overlap(q, _tokens(body))
    q_in_kb = _overlap(q, _tokens(kb_text))

    if usvo is not None:
        key = [usvo.name, usvo.status, usvo.birth_date, usvo.phone, usvo.address]
        profile = sum(1 for v in key if (v or "").strip()) / len(key)
    else:
        profile = 0.0

    depth = min(1.0, len(body.strip()) / 600.0)

    base = {"dify": 0.34, "model": 0.26}.get(source, 0.08)  # local → почти всё из контента
    score = (
        base
        + 0.24 * q_in_answer
        + 0.18 * q_in_kb
        + 0.12 * profile
        + 0.09 * depth
    )
    return round(max(0.05, min(0.97, score)), 2)


def _kb_excerpt(cfg: Config, max_chars: int = 12000) -> str:
    text = _read_kb(cfg.web.kb_text).strip()
    if not text:
        return ""
    if len(text) <= max_chars:
        return text
    return text[:max_chars] + "\n\n[Фрагмент базы знаний обрезан по лимиту контекста.]"


def _profile(usvo: UsvoRecord | None) -> str:
    if not usvo:
        return "Карточка УСВО не сопоставлена."
    fields = [
        f"ФИО: {usvo.name}",
        f"Статус: {usvo.status}",
        f"Дата рождения: {usvo.birth_date}",
        f"Телефон: {usvo.phone}",
        f"Адрес: {usvo.address}",
    ]
    fields += [f"{f.label}: {f.value}" for f in usvo.primary[:14]]
    fields += [f"{f.label}: {f.value}" for f in usvo.secondary[:8]]
    return "\n".join(x for x in fields if x and not x.endswith(": "))


def _draft_user_prompt(cfg: Config, question: str, usvo: UsvoRecord | None) -> str:
    """Содержимое запроса (user prompt) для Dify-переменной `query`."""
    kb = _kb_excerpt(cfg)
    kb_part = f"\n\nБаза знаний:\n{kb}" if kb else ""
    return (
        f"Вопрос гражданина: {question}\n\n"
        f"Профиль/карточка участника СВО:\n{_profile(usvo)}"
        f"{kb_part}"
    )


async def _ask_web_dify(cfg: Config, prompt: str, user_key: str) -> str:
    ai = _web_ai_cfg(cfg)
    base_url = (ai.dify_base_url or cfg.dify.base_url).rstrip("/")
    mode = (getattr(ai, "dify_mode", "auto") or "auto").strip().lower()
    headers = {
        "Authorization": f"Bearer {ai.dify_app_key}",
        "Content-Type": "application/json",
    }

    async def call_chat(client: httpx.AsyncClient) -> tuple[int, str, str]:
        body = {
            "inputs": {},
            "query": prompt,
            "response_mode": "blocking",
            "user": user_key,
        }
        resp = await client.post(f"{base_url}/chat-messages", headers=headers, json=body)
        if resp.status_code >= 400:
            return resp.status_code, resp.text, ""
        data = resp.json()
        return resp.status_code, resp.text, _extract_text(data.get("answer", ""))

    async def call_completion(client: httpx.AsyncClient) -> tuple[int, str, str]:
        input_var = (getattr(ai, "dify_input_variable", "query") or "query").strip()
        body = {
            "inputs": {input_var: prompt},
            "response_mode": "blocking",
            "user": user_key,
        }
        resp = await client.post(f"{base_url}/completion-messages", headers=headers, json=body)
        if resp.status_code >= 400:
            return resp.status_code, resp.text, ""
        data = resp.json()
        return resp.status_code, resp.text, _extract_text(data.get("answer", ""))

    async with httpx.AsyncClient(timeout=ai.timeout) as client:
        attempts = []
        if mode in ("auto", "chat"):
            status, raw, text = await call_chat(client)
            attempts.append(("chat", status, raw))
            if text or mode == "chat":
                if status >= 400:
                    log.warning("Web Dify chat error %s: %s", status, raw)
                return text
        if mode in ("auto", "completion"):
            status, raw, text = await call_completion(client)
            attempts.append(("completion", status, raw))
            if text or mode == "completion":
                if status >= 400:
                    log.warning("Web Dify completion error %s: %s", status, raw)
                return text

    for endpoint, status, raw in attempts:
        log.warning("Web Dify %s returned no answer, status=%s body=%s", endpoint, status, raw[:500])
    return ""


async def _ask_openai_compatible(cfg: Config, prompt: str) -> str:
    ai = _web_ai_cfg(cfg)
    base_url = ai.openai_base_url.rstrip("/")
    url = f"{base_url}/chat/completions"
    headers = {"Content-Type": "application/json"}
    if _configured(ai.openai_api_key):
        headers["Authorization"] = f"Bearer {ai.openai_api_key}"
    body = {
        "model": ai.openai_model,
        "messages": [
            {
                "role": "system",
                "content": "Ты готовишь официальные, точные и краткие ответы для веб-кабинета оператора.",
            },
            {"role": "user", "content": prompt},
        ],
        "temperature": ai.temperature,
    }
    async with httpx.AsyncClient(timeout=ai.timeout) as client:
        resp = await client.post(url, headers=headers, json=body)
        if resp.status_code >= 400:
            log.warning("OpenAI-compatible error %s: %s", resp.status_code, resp.text)
            return ""
        data = resp.json()
    try:
        return _extract_text(data["choices"][0]["message"]["content"])
    except (KeyError, IndexError, TypeError):
        log.warning("Unexpected OpenAI-compatible response: %s", data)
        return ""


def _extract_text(raw) -> str:
    text = str(raw or "").strip()
    text = text.replace("```json", "").replace("```", "").strip()
    try:
        parsed = json.loads(text)
    except Exception:
        parsed = None
    if isinstance(parsed, dict):
        return str(parsed.get("answer") or parsed.get("text") or parsed.get("response") or text).strip()
    return text


async def _ask_external_ai(cfg: Config, prompt: str, user_key: str) -> tuple[str, str]:
    ai = _web_ai_cfg(cfg)
    provider = (ai.provider or "local").strip().lower()
    if provider == "dify" and _configured(ai.dify_app_key):
        # системный промт живёт в самом Dify-приложении, отсюда уходит только запрос
        return await _ask_web_dify(cfg, prompt, user_key), "dify"
    if provider == "openai_compatible" and (ai.openai_base_url or "").strip() and (ai.openai_model or "").strip():
        return await _ask_openai_compatible(cfg, prompt), "model"
    return "", "local"


async def draft_reply(
    cfg: Config,
    dify: DifyClient,
    *,
    question: str,
    usvo: UsvoRecord | None,
    operator: str,
    citizen_name: str = "",
) -> dict:
    """Готовит черновик официального ответа на обращение."""
    name = (citizen_name or "").strip() or (usvo.name if usvo else "")
    greeting = _greeting(name)

    kb_text = _kb_excerpt(cfg)
    body = ""
    source = "local"
    if dify_ready(cfg):
        user_prompt = _draft_user_prompt(cfg, question, usvo)
        try:
            body, source = await _ask_external_ai(
                cfg, user_prompt,
                user_key=f"web-operator-{operator or 'op'}",
            )
        except Exception as exc:  # noqa: BLE001
            log.warning("Web AI draft failed, using local template: %s", exc)

    if not body:
        source = "local"
        body = (
            "[Здесь будет содержательный ответ по вопросу. Уточните меры поддержки, "
            "перечень документов и сроки. Подсказка ИИ недоступна — заполните вручную "
            "или настройте web.ai в config.yaml.]"
        )

    signature = f"С уважением,\n{operator}" if operator else "С уважением,\nЕдиный центр поддержки участников СВО"

    text = (
        f"{greeting}\n\n"
        f"Благодарим Вас за обращение в Единый центр поддержки участников СВО.\n\n"
        f"По Вашему вопросу «{question}» сообщаем следующее:\n"
        f"{body}\n\n"
        f"При необходимости Вы можете записаться на личный приём для получения "
        f"подробной консультации.\n\n"
        f"{signature}"
    )
    confidence = _draft_confidence(
        source=source, usvo=usvo, question=question, kb_text=kb_text, body=body
    )
    return {"draft": text, "source": source, "confidence": confidence}


def _read_kb(path: str) -> str:
    try:
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as f:
                return f.read()
    except OSError:
        pass
    return ""


def _suggestion(kind, priority, title, detail, action, docs, where) -> dict:
    return {
        "kind": kind,
        "priority": priority,  # high | medium | base
        "title": title,
        "detail": detail,
        "action": action,
        "docs": docs,
        "where": where,
    }


def _local_suggestions(usvo: UsvoRecord) -> list[dict]:
    """Правила «что предложить» на основе полей карточки."""
    items: list[dict] = []
    flags = usvo.flags
    text_primary = " ".join(f.value.lower() for f in usvo.primary)

    if flags.get("unemployed"):
        items.append(_suggestion(
            "work", "high",
            "Содействие в трудоустройстве",
            "Гражданин нуждается в работе. Подобрать вакансии с учётом профессии, "
            "предложить квотируемые рабочие места и участие в программе «Время Героя».",
            "Записать в центр занятости и направить к работодателю-партнёру.",
            ["Паспорт", "Трудовая книжка / СТД-Р", "Документы об образовании", "Удостоверение ВБД"],
            "Центр занятости «Работа России»",
        ))

    if any("дополнительное образование" in f.label.lower() and
           ("нужно" in f.value.lower() or "требует" in f.value.lower()) for f in usvo.primary):
        items.append(_suggestion(
            "edu", "medium",
            "Профобучение и переподготовка",
            "Отмечена потребность в обучении. Доступно бесплатное переобучение и "
            "повышение квалификации по востребованным специальностям.",
            "Подобрать курс и оформить заявку на бесплатное обучение.",
            ["Паспорт", "Документ об образовании", "СНИЛС"],
            "Центр занятости · Учебный центр-партнёр",
        ))

    if not flags.get("vbd"):
        items.append(_suggestion(
            "status", "high",
            "Оформление удостоверения «Ветеран боевых действий»",
            "Статус ВБД не подтверждён — без него недоступна большая часть льгот. "
            "Помочь собрать документы и подать заявление.",
            "Сформировать пакет документов и подать в военкомат.",
            ["Паспорт", "Военный билет", "Выписка из приказа об участии в СВО", "Фото 3×4"],
            "Военный комиссариат",
        ))

    health_hit = any(w in text_primary for w in ("инвалид", "ампутац", "увеч", "тяжел"))
    if health_hit:
        items.append(_suggestion(
            "med", "high",
            "Медицинская и социальная реабилитация",
            "По состоянию здоровья положены санаторно-курортное лечение, технические "
            "средства реабилитации (ТСР) и психологическая помощь.",
            "Оформить ИПРА и направление на ТСР, подобрать путёвку.",
            ["Паспорт", "Справка МСЭ", "ИПРА", "Полис ОМС"],
            "СФР · Госпиталь для ветеранов войн",
        ))

    if any("дети" in f.label.lower() and f.value and "нет" not in f.value.lower()
           for f in (*usvo.secondary, *usvo.primary)):
        items.append(_suggestion(
            "family", "medium",
            "Меры поддержки членам семьи и детям",
            "В семье есть дети. Положены пособия, льготы на питание и образование, "
            "первоочередное зачисление в детсад и оздоровление детей.",
            "Проинформировать семью и помочь оформить пособия.",
            ["Свидетельства о рождении детей", "Справка о составе семьи", "Паспорт"],
            "Соцзащита · МФЦ «Мои документы»",
        ))

    if not (flags.get("org_vremya") or flags.get("org_geroi_mo") or flags.get("org_assoc")):
        items.append(_suggestion(
            "org", "base",
            "Вовлечение в ветеранские организации",
            "Не состоит в объединениях. Пригласить в Ассоциацию ветеранов СВО, "
            "программы «Время Героя» и «Герои Подмосковья» — поддержка и наставничество.",
            "Направить приглашение и записать на ближайшую встречу сообщества.",
            ["Паспорт", "Удостоверение ВБД (при наличии)"],
            "Фонд «Защитники Отечества»",
        ))

    if flags.get("stale_contact"):
        items.append(_suggestion(
            "contact", "medium",
            "Плановый обзвон и актуализация данных",
            f"Контакта не было давно (последний обзвон: {usvo.call_date or '—'}). "
            "Проверить актуальность данных и рассказать о новых мерах поддержки.",
            "Запланировать звонок и обновить карточку.",
            [],
            "Единый центр поддержки участников СВО",
        ))

    if not items:
        items.append(_suggestion(
            "info", "base",
            "Проверить новые меры поддержки",
            f"Свериться с актуальным перечнем региональных и федеральных мер с момента "
            f"последнего обзвона ({usvo.call_date or '—'}).",
            "Сверить перечень льгот и при необходимости связаться с гражданином.",
            [],
            "Единый центр поддержки участников СВО",
        ))
    return items


async def suggest_measures(
    cfg: Config,
    dify: DifyClient,
    *,
    usvo: UsvoRecord,
) -> dict:
    """Предложения по направлениям для конкретного УСВО."""
    items = _local_suggestions(usvo)
    ai_note = ""
    source = "local"

    if dify_ready(cfg):
        profile_bits = [usvo.name, f"статус: {usvo.status}"]
        profile_bits += [f"{f.label}: {f.value}" for f in usvo.primary[:14]]
        profile = "; ".join(b for b in profile_bits if b)
        kb = _kb_excerpt(cfg, max_chars=9000)
        kb_part = f"\n\nБаза знаний:\n{kb}" if kb else ""
        user_prompt = (
            f"Профиль участника СВО:\n{profile}"
            f"{kb_part}\n\n"
            "Сформируй персональные рекомендации по направлениям поддержки."
        )
        try:
            ai_note, source = await _ask_external_ai(
                cfg, user_prompt,
                user_key=f"web-suggest-{usvo.id}",
            )
        except Exception as exc:  # noqa: BLE001
            log.warning("Web AI suggest failed, using local rules: %s", exc)

    return {"items": items, "ai_note": ai_note, "source": source}
