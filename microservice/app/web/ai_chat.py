"""Stateful-чат веб-кабинета с Dify по базе карточек УСВО."""
from __future__ import annotations

import json
import re
from collections import defaultdict

from app.common.docx import Paragraph, Table, build_docx
from app.common.timez import msk_today
from app.max.dify_client import DifyApiError, DifyClient
from app.max.store import Store
from app.web.usvo import UsvoRecord
from app.web.usvo_knowledge import (
    UsvoCardKnowledgeSyncService,
    UsvoCardsMetaService,
    build_card_chunk,
)
from app.web.usvo_query import build_query_context

ANSWER_TYPES = {
    "text": "Текст",
    "detailed_reference": "Развернутая справка",
    "brief_reference": "Краткая справка",
}

# Типы ответа, которые подразумевают формирование .docx-справки с ответом.
REFERENCE_TYPES = {"detailed_reference", "brief_reference"}

SYSTEM_PROMPT = """Ты ассистент, который отвечает на вопросы пользователей по базе карточек УСВО.

У тебя есть:
1. база знаний с карточками людей;
2. мета-информация о текущем состоянии базы карточек;
3. история диалога пользователя;
4. выбранный формат ответа.

Правила:
- Отвечай только на основе доступных данных.
- Если данных недостаточно, прямо скажи, каких данных не хватает.
- Не выдумывай людей, категории, статусы или числа.
- Если в запросе есть блок «Точные данные по всей базе» — это АВТОРИТЕТНЫЙ источник
  для списков, перечислений и чисел: он посчитан точно по ВСЕМ карточкам.
  Используй именно его числа и перечни; найденные карточки из базы знаний бери лишь как
  дополнение по конкретным людям. Если блок помечает список как усечённый — сообщи точное
  общее число и что показана часть.
- Не описывай в ответе, как именно посчитаны данные (например, «рассчитано
  детерминированно», «по детерминированному подсчёту» и т. п.). Просто давай готовый
  ответ на вопрос.
- Если вопрос статистический, используй мета-информацию и точные данные по всей базе.
- Если вопрос про конкретных людей, используй данные карточек.
- Если в ответе упоминаешь человека из карточек УСВО, пиши его полное ФИО (оно станет
  ссылкой на карточку).
- Форматируй ответ согласно выбранному типу ответа.
- Для краткой справки отвечай максимально сжато.
- Для развернутой справки используй официальный структурированный формат.
"""

# ВРЕМЕННЫЙ режим full_context: вся база карточек подаётся прямо в контекст модели,
# без базы знаний и детерминированного движка. Модель сама считает/перечисляет/агрегирует.
FULL_CONTEXT_SYSTEM_PROMPT = """Ты ассистент, который отвечает на вопросы сотрудников
контакт-центра по базе карточек УСВО (участников СВО).

ВАЖНО: полный список ВСЕХ текущих карточек УСВО передан прямо в этом запросе (блок
«Полная база карточек УСВО»). Базы знаний/поиска нет — отвечай ТОЛЬКО по этим карточкам,
мета-информации и истории диалога из запроса.

Правила:
- Отвечай только на основе переданных данных. Если данных не хватает — прямо скажи, чего
  именно. Не выдумывай людей, статусы, награды или числа.
- Для статистических вопросов («сколько …») считай точно по всем переданным карточкам и
  приводи число.
- Для вопросов-перечислений и составных условий (например «у кого инвалидность и более двух
  детей») переби все карточки, отбери подходящие и перечисли их.
- Когда упоминаешь человека из карточек, ВСЕГДА пиши его полное ФИО как в карточке — по нему
  автоматически строится гиперссылка на карточку. Рядом коротко указывай релевантные для
  вопроса данные (статус, населённый пункт, награды, нужное поле).
- Если блок «Полная база карточек УСВО» помечен как усечённый по объёму — честно сообщи, что
  показаны не все карточки, и предложи уточнить вопрос.
- Не описывай в ответе, как именно ты считал. Просто давай готовый ответ.
- Строго соблюдай выбранный формат ответа и его шаблон.
"""

# Заголовок и формат таблицы — это и есть шаблон справки (см.
# прочее/Шаблон_справки_для_ответа_в_чате_llm.docx). Сами показатели и значения НЕ
# фиксированы: модель формирует строки по смыслу вопроса и доступных данных.
_REFERENCE_TABLE_RULES = """Структура ответа-справки (строго):
1) Первая строка — заголовок ровно вида: Информация на {today} г.
2) Со второй строки — таблица в формате Markdown ровно с тремя колонками:
| № | Показатель | Значение |
| --- | --- | --- |
| 1 | <показатель> | <значение> |
| 2 | <показатель> | <значение> |
Правила таблицы:
- Колонка «№» — сквозная нумерация строк с 1.
- «Показатель» и «Значение» НЕ заданы заранее — выводи их по смыслу вопроса и данных
  (показатель — что считаем/о чём строка; значение — число, факт или короткая формулировка).
- Для статистики ставь точные числа; добавляй строки-разбивки по категориям, где это уместно.
- Не выдумывай показатели и числа, которых нет в данных; недостающее помечай «нет данных».
- Внутри ячеек не используй символ «|» и переносы строк."""

FORMAT_PROMPTS = {
    "text": """Формат «Текст»: отвечай естественным языком без официальной структуры справки.
Допустимы списки и пояснения. Если спрашивают число, сначала дай число и короткое объяснение.""",
    "detailed_reference": """Формат «Развернутая справка».
"""
    + _REFERENCE_TABLE_RULES
    + """
Объём (развёрнутая): включай ВСЕ релевантные вопросу показатели и их разбивки —
таблица должна быть полной. После таблицы добавь обычным текстом блок пояснений:
строку-метку «Примечание:» и 1–3 предложения об основании данных и недостающих сведениях.
Если в ответе уместно перечислить конкретных людей — добавь отдельную таблицу
| № | ФИО | Релевантные данные | (ФИО пиши полностью, как в карточке).""",
    "brief_reference": """Формат «Краткая справка».
"""
    + _REFERENCE_TABLE_RULES
    + """
Объём (краткая): в таблице оставляй только показатели, которые напрямую отвечают на вопрос
(обычно 1–5 строк). Без блока пояснений и без перечисления всех людей без прямой просьбы.""",
}


class AiChatNotFound(RuntimeError):
    pass


class AiChatValidation(RuntimeError):
    pass


class AiChatProviderError(RuntimeError):
    pass


def normalize_answer_type(value: str | None) -> str:
    answer_type = (value or "text").strip()
    if answer_type not in ANSWER_TYPES:
        raise AiChatValidation("Неизвестный тип ответа")
    return answer_type


def _metadata(raw: str | None) -> dict:
    try:
        data = json.loads(raw or "{}")
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _chat_dict(row) -> dict:
    data = dict(row)
    return {
        "id": int(data["id"]),
        "title": data.get("title") or "Новый чат",
        "answerType": data.get("answer_type") or "text",
        "createdAt": float(data.get("created_at") or 0),
        "updatedAt": float(data.get("updated_at") or 0),
        "messageCount": int(data.get("message_count") or 0),
    }


def _message_dict(row) -> dict:
    data = dict(row)
    return {
        "id": int(data["id"]),
        "chatId": int(data["chat_id"]),
        "role": data.get("role") or "assistant",
        "content": data.get("content") or "",
        "createdAt": float(data.get("created_at") or 0),
        "metadata": _metadata(data.get("metadata")),
    }


def _auto_title(message: str) -> str:
    text = " ".join((message or "").split())
    if not text:
        return "Новый чат"
    return text[:67] + ("…" if len(text) > 67 else "")


def linkify_usvo_names(text: str, records: list[UsvoRecord]) -> str:
    """Ставит ссылки только для однозначных точных ФИО и не трогает готовые ссылки."""
    if not text:
        return text
    by_name: dict[str, list[UsvoRecord]] = defaultdict(list)
    for record in records:
        name = " ".join((record.name or "").split())
        if name:
            by_name[name.casefold()].append(record)
    unique = {
        rows[0].name: rows[0]
        for rows in by_name.values()
        if len(rows) == 1 and len(rows[0].name.split()) >= 2
    }
    if not unique:
        return text

    protected: list[str] = []

    def protect(match: re.Match) -> str:
        protected.append(match.group(0))
        return f"\u0001{len(protected) - 1}\u0002"

    result = re.sub(r"\[[^\]]+\]\([^)]+\)", protect, text)
    result = re.sub(r"<a\b[^>]*>.*?</a>", protect, result, flags=re.IGNORECASE | re.DOTALL)
    word = r"0-9A-Za-zА-Яа-яЁё_"
    for name in sorted(unique, key=len, reverse=True):
        record = unique[name]
        pattern = re.compile(rf"(?<![{word}]){re.escape(name)}(?![{word}])", re.IGNORECASE)
        result = pattern.sub(
            lambda match: f"[{match.group(0)}](/usvo/cards/{record.id})",
            result,
        )

    def restore(match: re.Match) -> str:
        return protected[int(match.group(1))]

    return re.sub(r"\u0001(\d+)\u0002", restore, result)


_MD_LINK_RE = re.compile(r"\[([^\]]+)\]\([^)]+\)")


def strip_markdown_links(text: str) -> str:
    """Убирает markdown-ссылки `[текст](url)`, оставляя только текст."""
    return _MD_LINK_RE.sub(r"\1", text or "")


def _is_table_row(line: str) -> bool:
    s = line.strip()
    return s.startswith("|") and s.count("|") >= 2


def _table_cells(line: str) -> list[str]:
    """Разбирает строку Markdown-таблицы `| a | b |` на ячейки."""
    s = line.strip()
    if s.startswith("|"):
        s = s[1:]
    if s.endswith("|"):
        s = s[:-1]
    return [c.strip() for c in s.split("|")]


def _is_separator_row(cells: list[str]) -> bool:
    """Строка-разделитель Markdown-таблицы `| --- | :--: |`."""
    return bool(cells) and all(
        c and set(c) <= set("-: ") and "-" in c for c in cells
    )


# Ширины колонок (твипы) для канонической таблицы справки «№ | Показатель | Значение».
_REFERENCE_COL_WIDTHS = [700, 6000, 2655]


def build_reference_docx(content: str, answer_type: str) -> bytes:
    """Собирает .docx-справку по шаблону: заголовок + таблица показателей.

    Первая непустая строка — заголовок справки (крупный, по центру). Блоки
    Markdown-таблиц превращаются в настоящие таблицы Word; строки-метки (короткие,
    оканчиваются двоеточием) — жирные подзаголовки, остальное — обычные абзацы.
    Markdown-ссылки на карточки заменяются их текстом.
    """
    plain = strip_markdown_links(content).replace("\r\n", "\n").replace("\r", "\n")
    lines = plain.split("\n")
    blocks: list = []
    title_done = False
    i = 0
    n = len(lines)
    while i < n:
        line = lines[i].rstrip()
        stripped = line.strip()
        if _is_table_row(line):
            # Собираем подряд идущие строки таблицы в один блок.
            rows: list[list[str]] = []
            while i < n and _is_table_row(lines[i]):
                cells = _table_cells(lines[i])
                if not _is_separator_row(cells):
                    rows.append(cells)
                i += 1
            if rows:
                width = len(rows[0])
                widths = _REFERENCE_COL_WIDTHS if width == 3 else None
                blocks.append(Table(rows, header=True, widths=widths))
                blocks.append(Paragraph(""))  # Word требует абзац после таблицы
            continue
        if not stripped:
            blocks.append(Paragraph(""))
            i += 1
            continue
        if not title_done:
            blocks.append(Paragraph(stripped, bold=True, size=16, align="center"))
            blocks.append(Paragraph(""))
            title_done = True
            i += 1
            continue
        if stripped.endswith(":") and len(stripped) <= 60:
            blocks.append(Paragraph(stripped, bold=True))
        else:
            blocks.append(Paragraph(line))
        i += 1
    if not title_done:
        blocks.insert(0, Paragraph(ANSWER_TYPES.get(answer_type, "Справка"),
                                   bold=True, size=16, align="center"))
    return build_docx(blocks)


class AiChatService:
    def __init__(
        self,
        store: Store,
        dify: DifyClient,
        records_provider,
        meta: UsvoCardsMetaService,
        knowledge: UsvoCardKnowledgeSyncService,
        *,
        max_history_messages: int = 30,
        planner: DifyClient | None = None,
        index=None,
        full_context_dify: DifyClient | None = None,
        full_context_max_chars: int = 45000,
    ):
        self.store = store
        self.dify = dify
        self.records_provider = records_provider
        self.meta = meta
        self.knowledge = knowledge
        self.max_history_messages = max(1, min(int(max_history_messages), 200))
        # Необязательный Dify-планировщик запросов (structured_output). Если не задан —
        # детерминированный движок использует офлайн-эвристику разбора вопроса.
        self.planner = planner
        # Производный SQLite-индекс карточек: фильтрация/подсчёты в БД, а не в контексте
        # модели (app/web/usvo_index.py). Без него движок откатывается на Python-скан.
        self.index = index
        # ВРЕМЕННОЕ решение: если задан отдельный Dify-ассистент полного контекста, чат
        # подаёт ему в контекст сразу ВСЕ карточки (без базы знаний и движка запросов).
        self.full_context_dify = full_context_dify
        self.full_context_max_chars = max(2000, int(full_context_max_chars))

    def ready(self) -> bool:
        if self.full_context_enabled():
            return self.full_context_dify.app_ready()
        return self.dify.app_ready()

    def full_context_enabled(self) -> bool:
        return self.full_context_dify is not None and self.full_context_dify.app_ready()

    def list_chats(self, user_id: str) -> list[dict]:
        return [_chat_dict(row) for row in self.store.list_ai_chats(user_id)]

    def create_chat(
        self, user_id: str, title: str = "", answer_type: str = "text"
    ) -> dict:
        answer_type = normalize_answer_type(answer_type)
        clean_title = " ".join((title or "").split())[:120] or "Новый чат"
        chat_id = self.store.create_ai_chat(user_id, clean_title, answer_type)
        return self.get_chat(user_id, chat_id)

    def get_chat(self, user_id: str, chat_id: int) -> dict:
        row = self.store.get_ai_chat(chat_id, user_id)
        if row is None:
            raise AiChatNotFound("Чат не найден")
        return _chat_dict(row)

    def update_chat(
        self,
        user_id: str,
        chat_id: int,
        *,
        title: str | None = None,
        answer_type: str | None = None,
    ) -> dict:
        clean_title = None
        if title is not None:
            clean_title = " ".join(title.split())[:120]
            if not clean_title:
                raise AiChatValidation("Название чата не может быть пустым")
        clean_type = normalize_answer_type(answer_type) if answer_type is not None else None
        if not self.store.update_ai_chat(
            chat_id, user_id, title=clean_title, answer_type=clean_type
        ):
            raise AiChatNotFound("Чат не найден")
        return self.get_chat(user_id, chat_id)

    def delete_chat(self, user_id: str, chat_id: int) -> None:
        if not self.store.delete_ai_chat(chat_id, user_id):
            raise AiChatNotFound("Чат не найден")

    def list_messages(self, user_id: str, chat_id: int) -> list[dict]:
        rows = self.store.list_ai_chat_messages(chat_id, user_id)
        if rows is None:
            raise AiChatNotFound("Чат не найден")
        return [_message_dict(row) for row in rows]

    def message_docx(
        self, user_id: str, chat_id: int, message_id: int
    ) -> tuple[bytes, str]:
        """Возвращает .docx-справку по ответу ассистента и имя файла."""
        row = self.store.get_ai_chat_message(message_id, chat_id, user_id)
        if row is None:
            raise AiChatNotFound("Сообщение не найдено")
        message = _message_dict(row)
        if message["role"] != "assistant":
            raise AiChatValidation("Справку можно сформировать только из ответа ассистента")
        answer_type = message["metadata"].get("answerType") or "text"
        if answer_type not in REFERENCE_TYPES:
            raise AiChatValidation("Этот ответ не является справкой")
        data = build_reference_docx(message["content"], answer_type)
        prefix = "spravka-kratkaya" if answer_type == "brief_reference" else "spravka"
        return data, f"{prefix}-{message_id}.docx"

    def _prompt(
        self,
        question: str,
        answer_type: str,
        history: list[dict],
        meta_text: str,
        query_context: str = "",
    ) -> str:
        history_text = "\n".join(
            f"{'Пользователь' if m['role'] == 'user' else 'Ассистент'}: {m['content']}"
            for m in history[-self.max_history_messages :]
            if m["role"] in {"user", "assistant"} and m["content"].strip()
        ) or "История пуста."
        format_instruction = FORMAT_PROMPTS[answer_type].replace(
            "{вопрос пользователя}", question
        ).replace("{today}", msk_today().strftime("%d.%m.%Y"))
        exact_block = (
            f"Точные данные по всей базе (по всем карточкам):\n"
            f"{query_context}\n\n"
            if query_context.strip()
            else ""
        )
        return (
            f"Текущий вопрос пользователя:\n{question}\n\n"
            f"Выбранный тип ответа: {ANSWER_TYPES[answer_type]}\n\n"
            f"История текущего диалога:\n{history_text}\n\n"
            f"Актуальная мета-информация:\n{meta_text}\n\n"
            f"{exact_block}"
            f"Системная инструкция:\n{SYSTEM_PROMPT.strip()}\n\n"
            f"Инструкция формата:\n{format_instruction.strip()}\n\n"
            "Найди релевантные карточки в подключённой базе знаний Dify и ответь на текущий вопрос."
        )

    def _build_cards_block(self, records: list[UsvoRecord]) -> str:
        """Сериализует ВСЕ карточки в один текстовый блок для контекста модели.

        Каждая карточка — самодостаточный чанк (build_card_chunk, со ссылкой
        /usvo/cards/<id> внутри). Список усекается по бюджету символов с честной
        пометкой, чтобы не переполнить окно модели.
        """
        chunks: list[str] = []
        used = 0
        shown = 0
        total = len(records)
        for record in records:
            chunk = f"=== Карточка #{record.id} ===\n{build_card_chunk(record)}"
            if shown and used + len(chunk) + 2 > self.full_context_max_chars:
                break
            chunks.append(chunk)
            used += len(chunk) + 2
            shown += 1
        body = "\n\n".join(chunks)
        if shown < total:
            body += (
                f"\n\n[Список усечён по объёму: показаны {shown} из {total} карточек. "
                "Сообщи пользователю точное общее число и предложи уточнить вопрос/фильтры "
                "для полного перечня.]"
            )
        return body

    def _full_context_prompt(
        self,
        question: str,
        answer_type: str,
        history: list[dict],
        meta_text: str,
        cards_block: str,
        total: int,
    ) -> str:
        history_text = "\n".join(
            f"{'Пользователь' if m['role'] == 'user' else 'Ассистент'}: {m['content']}"
            for m in history[-self.max_history_messages :]
            if m["role"] in {"user", "assistant"} and m["content"].strip()
        ) or "История пуста."
        format_instruction = FORMAT_PROMPTS[answer_type].replace(
            "{вопрос пользователя}", question
        ).replace("{today}", msk_today().strftime("%d.%m.%Y"))
        return (
            f"Текущий вопрос пользователя:\n{question}\n\n"
            f"Выбранный тип ответа: {ANSWER_TYPES[answer_type]}\n\n"
            f"История текущего диалога:\n{history_text}\n\n"
            f"Актуальная мета-информация:\n{meta_text}\n\n"
            f"Полная база карточек УСВО (всего карточек: {total}):\n{cards_block}\n\n"
            f"Системная инструкция:\n{FULL_CONTEXT_SYSTEM_PROMPT.strip()}\n\n"
            f"Инструкция формата:\n{format_instruction.strip()}\n\n"
            "Ответь на текущий вопрос строго по приведённым выше карточкам."
        )

    async def send_message(
        self,
        user_id: str,
        chat_id: int,
        content: str,
        answer_type: str | None = None,
    ) -> dict:
        question = (content or "").strip()
        if not question:
            raise AiChatValidation("Сообщение не может быть пустым")
        if len(question) > 12000:
            raise AiChatValidation("Сообщение слишком длинное")
        chat = self.get_chat(user_id, chat_id)
        selected_type = normalize_answer_type(answer_type or chat["answerType"])
        if selected_type != chat["answerType"]:
            chat = self.update_chat(user_id, chat_id, answer_type=selected_type)

        history = self.list_messages(user_id, chat_id)
        user_message_id = self.store.add_ai_chat_message(
            chat_id,
            user_id,
            "user",
            question,
            json.dumps({"answerType": selected_type}, ensure_ascii=False),
        )
        if user_message_id is None:
            raise AiChatNotFound("Чат не найден")
        if not history and chat["title"] == "Новый чат":
            self.store.update_ai_chat(chat_id, user_id, title=_auto_title(question))

        meta_text = self.meta.text()
        records = self.records_provider()

        if self.full_context_enabled():
            # ВРЕМЕННЫЙ режим: подаём модели сразу ВСЕ карточки, без базы знаний и
            # детерминированного движка — она сама считает/перечисляет/агрегирует.
            sync_result = {"ok": True, "skipped": True, "reason": "full_context"}
            cards_block = self._build_cards_block(records)
            prompt = self._full_context_prompt(
                question, selected_type, history, meta_text, cards_block, len(records)
            )
            chat_dify = self.full_context_dify
        else:
            sync_result = await self.knowledge.ensure_current()
            # Детерминированный движок: для вопросов-фильтров/счёта/агрегатов считаем точный
            # ответ по ВСЕМ карточкам (обходит лимит 32k — в контекст не лезут 1500 карточек).
            try:
                query_context = await build_query_context(
                    question, records, self.meta, self.planner, index=self.index
                )
            except Exception:  # noqa: BLE001
                query_context = ""
            prompt = self._prompt(
                question, selected_type, history, meta_text, query_context
            )
            chat_dify = self.dify
        try:
            result = await chat_dify.ask_text(
                prompt,
                user_key=f"web-ai-{user_id}-{chat_id}",
            )
        except DifyApiError as exc:
            raise AiChatProviderError(str(exc)) from exc
        except Exception as exc:
            raise AiChatProviderError("Не удалось получить ответ от Dify") from exc

        answer = linkify_usvo_names(result["answer"], records)
        metadata = {
            "answerType": selected_type,
            "difyMessageId": result.get("message_id"),
            "difyConversationId": result.get("conversation_id"),
            "knowledgeSync": sync_result,
            "fullContext": self.full_context_enabled(),
            "hasDocx": selected_type in REFERENCE_TYPES,
        }
        assistant_id = self.store.add_ai_chat_message(
            chat_id,
            user_id,
            "assistant",
            answer,
            json.dumps(metadata, ensure_ascii=False),
        )
        return {
            "chat": self.get_chat(user_id, chat_id),
            "userMessage": {
                "id": user_message_id,
                "chatId": chat_id,
                "role": "user",
                "content": question,
                "metadata": {"answerType": selected_type},
            },
            "message": {
                "id": assistant_id,
                "chatId": chat_id,
                "role": "assistant",
                "content": answer,
                "metadata": metadata,
            },
        }
