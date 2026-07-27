"""Логика MAX-бота: ответы из БЗ, эскалация операторам, запись на приём.

Вся stateful-часть (то, что Dify вести не может) живёт здесь:
  - счётчик вопросов и предложение записи после каждых N вопросов
    (счётчик ведётся ОТ ПРОШЛОГО предложения и сбрасывается при показе оффера);
  - эскалация неуверенных ответов в чат операторов с inline-кнопкой «Ответить»;
  - захват ответа оператора (кнопка «Ответить» ИЛИ reply на сообщение-эскалацию),
    доставка пользователю + запись в базу знаний + отчёт в чат операторов;
  - выбор слота и отправка заявки операторам (контакты + последние 3 вопроса + время).
"""
from __future__ import annotations

import json
import logging
import re

from app.config import Config
from app.docs import measures as measures_mod
from app.docs.doc_ai import DocAI
from app.docs.forms import application_summary, build_application_docx, normalize_application
from app.docs.templates import check_required
from app.max.client import MaxClient
from app.max.dify_client import DifyClient, _configured as _dify_configured
from app.max.schedule import find_building, iter_slots
from app.max.store import Store
from app.web.sla import sla_fields

log = logging.getLogger("max.bot")

# Тексты бота — в одном месте, чтобы их было удобно править.

# Приветствие (экран 1): показывается новым пользователям и по команде /start.
# Под ним — три тематические кнопки выбора темы обращения (см. _topic_rows).
MSG_WELCOME = (
    "👋 Здравствуйте! Я ваш цифровой помощник администрации городского округа "
    "Ленинский. Отвечу на ваши вопросы, помогу решить проблему или подберу меру "
    "поддержки и оформлю документы на её получение.\n\n"
    "Пожалуйста, выберите тему обращения ниже:"
)

# Тексты тематических кнопок стартового экрана.
MSG_TOPIC_MEASURES = "🎖️ Меры поддержки"
MSG_TOPIC_GKH = "🚰 Вопросы ЖКХ"
MSG_TOPIC_ROADS = "🛣️ Дороги и благоустройство"

# Заглушка для разделов, которые ещё в разработке (кнопки ЖКХ и Дороги).
MSG_TOPIC_STUB = (
    "🚧 Этот раздел сейчас в разработке — скоро здесь можно будет отправить обращение.\n\n"
    "А пока я готов помочь с мерами поддержки участников СВО и их семей."
)

# Повторное обращение: анкета (родство/регистрация/телефон) уже пройдена — не
# переспрашиваем, а подставляем сохранённый профиль и сразу переходим к мерам.
MSG_PROFILE_KNOWN = (
    "С возвращением! Использую данные из вашего профиля:\n\n"
    "• Статус: {status}\n"
    "• Регион: {region}\n"
    "• Телефон: {phone}\n\n"
    "Если что-то изменилось, отправьте /start, чтобы обновить данные."
)

# Экран 2: старт подбора мер поддержки — вопросы о родстве и регистрации.
MSG_MEASURES_INTRO = (
    "Давайте подберём доступные льготы. Ответьте, пожалуйста, на вопросы:\n\n"
    "1. Кем вы приходитесь участнику СВО? (например: ребёнок, супруга, родитель)\n"
    "2. Зарегистрированы ли вы постоянно на территории Московской области?\n\n"
    "Вы можете написать ответ текстом или выбрать вариант на кнопках ниже:"
)
MSG_ASK_RELATION = (
    "Уточните, пожалуйста, кем вы приходитесь участнику СВО — напишите текстом "
    "или выберите вариант на кнопках ниже:"
)
MSG_ASK_REGION = (
    "Зарегистрированы ли вы постоянно на территории Московской области? "
    "Напишите, пожалуйста, город регистрации (например: «Да, прописана в Видном»)."
)
MSG_REGION_OUTSIDE = (
    "Обратите внимание: региональные меры поддержки предоставляются жителям, "
    "постоянно зарегистрированным в Московской области. Если это ваш случай — "
    "напишите город регистрации, и мы продолжим оформление."
)

# Экран 3: сводка профиля с бейджами + запрос контакта.
MSG_ASK_PHONE_HINT = (
    "Нажмите кнопку ниже, чтобы поделиться контактом. Можно также прислать номер "
    "телефона обычным сообщением."
)
MSG_SHARE_PHONE = "📱 Поделиться номером телефона"
MSG_NEED_PHONE = (
    "Чтобы продолжить оформление, поделитесь, пожалуйста, номером телефона — "
    "кнопкой ниже или обычным сообщением."
)

# Экран 4: приглашение прикрепить документы для выбранной меры.
MSG_PICK_MEASURE = "Отлично! Выберите меру поддержки, которую хотите оформить:"
# Кнопка выхода из выбора меры в обычный режим вопросов.
MSG_MEASURE_CANCEL_ASK = "❌ Отмена (задать вопрос)"
MSG_ASK_QUESTION = "Хорошо, оформление отложено. Напишите свой вопрос — я постараюсь помочь."

# Экран 5: заявка зарегистрирована + предложение подписки.
MSG_FINAL_REGISTERED = (
    "✅ Документ успешно проверен! Заявление заполнено и заявка зарегистрирована.\n\n"
    "Номер заявления: {number}\n\n"
    "• Регламентный срок рассмотрения: 3 дня.\n"
    "• Что дальше: пакет документов направлен в администрацию г.о. Ленинский. "
    "Официальное решение о выделении меры поддержки придёт вам в этот чат "
    "автоматически сразу после подписания.\n\n"
    "🔔 Хотите подписаться на уведомления о новых мерах поддержки в Подмосковье "
    "и изменениях статуса этого заявления?"
)
MSG_SUBSCRIBED = (
    "🔔 Готово! Вы подписаны на уведомления о новых мерах поддержки и статусе "
    "вашего заявления."
)
MSG_UNSUBSCRIBED = (
    "Хорошо, уведомления отправлять не будем. Статус заявления вы всегда можете "
    "узнать здесь же в чате."
)
MSG_UNSUBSCRIBED_DONE = (
    "🔕 Готово! Вы отписались от рассылки — уведомления о новых мерах поддержки "
    "приходить не будут. Подписаться снова можно в любой момент кнопкой ниже."
)

MSG_ESCALATED = "Спасибо за вопрос! Он передан оператору — мы ответим вам в ближайшее время."
MSG_OFFER = "Не хотите записаться на приём?"
MSG_PICK_TIME = "Выберите удобное для вас время:"
MSG_OFFER_DECLINED = "Хорошо! Можете продолжать задавать вопросы."
MSG_APPT_CONFIRMED = "Заявка на приём принята. Оператор свяжется с вами для подтверждения."
MSG_OPERATOR_PROMPT = "Введите ответ одним сообщением — он будет отправлен пользователю."

# Запрос не по теме контакт-центра: отвечаем базовым сообщением и НЕ эскалируем операторам.
MSG_OFF_TOPIC = (
    "Я — ассистент контакт-центра господдержки участников СВО и их семей. "
    "Помогаю по вопросам мер поддержки, выплат и льгот, медицинской и социальной "
    "реабилитации, трудоустройства, жилья, юридической и психологической помощи, "
    "оформления документов и записи на приём.\n\n"
    "Кажется, ваш вопрос не относится к этим темам. Пожалуйста, переформулируйте его "
    "в рамках господдержки СВО — и я постараюсь помочь."
)

# Сценарий оформления меры поддержки по фотографиям документов.
MSG_DOC_ANALYZING = (
    "Получил фотографии документов. Анализирую их с помощью ИИ — это займёт несколько секунд…"
)
# Не удалось подобрать меру по присланным фото (ИИ не настроен / нет подходящей меры).
MSG_DOC_NO_MEASURE = (
    "Не удалось автоматически подобрать меру поддержки по этим документам. "
    "Опишите, пожалуйста, вашу ситуацию текстом — и я подскажу подходящую меру, "
    "либо нажмите «Оформление мер поддержки», чтобы выбрать её вручную."
)

# Сценарий «Меры поддержки» (выбор меры → пошаговый сбор документов → заявление).
MSG_SM_BUTTON = "Оформление мер поддержки"
MSG_SM_MENU = "Доступные меры поддержки"
MSG_SM_NONE = "Сейчас нет доступных мер поддержки. Загляните чуть позже."
MSG_SM_NOT_PHOTO = "Пожалуйста, отправьте фото документа."
MSG_SM_NEED_TEXT = "Пожалуйста, отправьте недостающие данные одним текстовым сообщением."
MSG_SM_PROCESSING = "Идёт обработка, пожалуйста, подождите немного…"
MSG_SM_NUDGE_CONFIRM = "Проверьте сформированное заявление и нажмите кнопку под ним."
MSG_SM_ANALYZING = (
    "Спасибо, все документы получены. Распознаю данные — это займёт несколько секунд…"
)
MSG_SM_MISSING_INTRO = (
    "Не удалось автоматически заполнить некоторые поля заявления. Пожалуйста, отправьте "
    "одним сообщением следующие данные:"
)
MSG_SM_CONFIRM = "Заявление сформировано. Проверьте файл и подтвердите отправку."
MSG_SM_SENT = "Заявление отправлено."
MSG_SM_UNAVAILABLE = "К сожалению, эта мера поддержки сейчас недоступна. Выберите другую."
MSG_SM_CANCELLED = "Оформление отменено. Вы можете начать заново в любой момент."
MSG_SM_NO_TEMPLATE = (
    "Шаблон заявления для этой меры пока не настроен — оператор оформит заявление вручную."
)

# Слова-команды, прерывающие пошаговый сбор документов.
_CANCEL_WORDS = ("отмена", "отменить", "стоп", "cancel", "прекратить")


class MaxBot:
    def __init__(
        self, cfg: Config, store: Store, max_client: MaxClient, dify: DifyClient,
        doc_ai: DocAI | None = None,
    ):
        self.cfg = cfg
        self.store = store
        self.max = max_client
        self.dify = dify
        self.doc_ai = doc_ai or DocAI(cfg)
        self.operator_chat_id = str(cfg.max.operator_chat_id)

    # ====================================================================
    # Точка входа: разбор апдейта MAX
    # ====================================================================
    async def handle_update(self, update: dict) -> None:
        update_type = update.get("update_type") or update.get("type")
        try:
            if update_type == "message_callback":
                await self._handle_callback(update)
            elif update_type == "bot_started":
                await self._handle_bot_started(update)
            elif update_type in ("message_created", "message", None):
                message = update.get("message") or update
                await self._handle_message(message)
            else:
                log.info("Пропущен update_type=%s", update_type)
        except Exception:  # noqa: BLE001 — webhook не должен падать целиком
            log.exception("Ошибка обработки апдейта")

    async def _handle_bot_started(self, update: dict) -> None:
        """Первый контакт: гражданин нажал «Начать общение» в диалоге с ботом.

        MAX присылает это отдельным апдейтом `bot_started` (а НЕ message_created),
        причём с плоской структурой: `chat_id` и `user` лежат прямо в апдейте, а не
        под `message.recipient`/`message.sender`. Без этой ветки апдейт молча
        отбрасывался, и приветствие не показывалось, пока гражданин сам что-нибудь
        не напишет.
        """
        user = update.get("user") or {}
        user_id = _id(user.get("user_id"))
        chat_id = _id(update.get("chat_id")) or user_id
        name = user.get("name") or user.get("first_name") or ""
        username = user.get("username") or ""
        await self._handle_start(user_id, chat_id, name, username)

    async def _handle_start(self, user_id: str, chat_id: str, name: str, username: str) -> None:
        """«Начать сначала»: сбрасываем анкету с опросом и показываем экран 1.

        Общий обработчик для команды /start и для кнопки «Начать общение»
        (апдейт `bot_started`) — чтобы гражданин мог заново ввести родство,
        прописку и телефон.
        """
        self.store.ensure_user(user_id, chat_id, name, username)
        self.store.clear_bot_flow(user_id)
        self.store.clear_user_profile(user_id)
        await self._send_welcome(chat_id, user_id)

    async def _send_welcome(self, chat_id: str, user_id: str) -> None:
        """Экран 1: приветствие с тремя тематическими кнопками."""
        await self.max.send_message(
            MSG_WELCOME, chat_id=chat_id, user_id=user_id,
            keyboard_rows=self._topic_rows(),
        )

    # ====================================================================
    # Входящее текстовое сообщение
    # ====================================================================
    async def _handle_message(self, message: dict) -> None:
        sender = message.get("sender") or {}
        recipient = message.get("recipient") or {}
        body = message.get("body") or {}

        text = (body.get("text") or message.get("text") or "").strip()
        image_urls = _image_urls(message)

        user_id = _id(sender.get("user_id"))
        chat_id = _id(recipient.get("chat_id")) or user_id
        name = sender.get("name") or sender.get("first_name") or ""
        username = sender.get("username") or ""

        # Вложения от гражданина (не в чате операторов).
        if chat_id != self.operator_chat_id:
            # Активный сценарий «Меры поддержки»: фото/текст направляем в него, а не в
            # старый авто-ЖКУ по фото. Гейт по незавершённому статусу сбора документов.
            flow_row = self.store.get_active_flow(user_id)
            if flow_row:
                await self._handle_measure_flow_message(
                    user_id, chat_id, name, username, flow_row, image_urls, text
                )
                return
            # Активный гид-опрос «Меры поддержки» (родство/регион/телефон, ДО выбора
            # меры): ответы текстом и присланный контакт направляем в него.
            guided_row = self.store.get_bot_flow(user_id)
            if guided_row:
                await self._handle_guided_message(
                    user_id, chat_id, name, username, guided_row,
                    image_urls, text, _contact_phone(message),
                )
                return
            # Если у гражданина есть заявление в ожидании подтверждения, а он вместо
            # кнопки «Подтвердить и подать» прислал свой файл (фото или документ) —
            # считаем присланный файл самим заявлением и подаём именно его.
            pending = self.store.get_pending_application(user_id)
            if pending:
                file_urls = _attachment_urls(message)
                if file_urls:
                    await self._submit_application(
                        user_id, chat_id, name, username, int(pending["id"]),
                        user_files=file_urls,
                    )
                    return
            # Иначе фотографии документов запускают сценарий оформления меры по фото.
            if image_urls:
                await self._handle_documents(user_id, chat_id, name, username, image_urls)
                return

        if not text:
            return

        log.info(
            "Входящее сообщение: chat_id=%s operator_chat_id=%s match=%s sender=%s text=%r",
            chat_id, self.operator_chat_id, chat_id == self.operator_chat_id, user_id, text[:60],
        )

        # Сообщение в чате операторов: возможно, это ответ оператора на эскалацию.
        if chat_id and chat_id == self.operator_chat_id:
            reply_mid = _reply_mid(message)
            log.info("Чат операторов: reply_mid=%s, ищу активную эскалацию", reply_mid)
            await self._handle_operator_message(user_id, name, text, reply_mid)
            return

        # Команда /start — показать приветствие с выбором темы обращения (экран 1).
        # /start = «начать сначала»: сбрасываем сохранённый профиль анкеты и текущий
        # опрос, чтобы гражданин мог заново ввести телефон/прописку/родство.
        if text.split()[0].lower().lstrip("/") == "start" and text.startswith("/"):
            await self._handle_start(user_id, chat_id, name, username)
            return

        # Текстовый триггер входа в сценарий «Меры поддержки» (на случай, если
        # гражданин набрал фразу вручную, а не нажал inline-кнопку).
        if _is_sm_trigger(text):
            await self._start_guided_flow(user_id, chat_id, name, username)
            return

        await self._handle_user_question(user_id, chat_id, name, username, text)

    async def _handle_user_question(
        self, user_id: str, chat_id: str, name: str, username: str, text: str
    ) -> None:
        # Новому пользователю (первое обращение) сначала показываем приветствие,
        # а затем отвечаем на его вопрос как обычно.
        if self.store.get_user(user_id) is None:
            await self._send_welcome(chat_id, user_id)

        self.store.ensure_user(user_id, chat_id, name, username)
        since_offer = self.store.add_question(user_id, text)

        result = await self.dify.ask(text, user_key=f"max-{user_id}")

        # Тематический фильтр целиком на стороне LLM (assistant.yml → max_llm): модель
        # сама решает, относится ли запрос к профилю контакт-центра и адекватен ли он, и
        # возвращает on_topic / topic_confidence ровно там же, где оценивает уверенность
        # в ответе из БЗ. Считаем запрос off-topic, только если LLM пометил on_topic=false
        # с достаточной уверенностью (topic_confidence ≥ порога) — при сомнении/сбое
        # парсинга (on_topic=true по умолчанию) пропускаем, чтобы не отсечь живого человека.
        # Off-topic-запросы отклоняются базовым сообщением и НЕ доходят до операторов.
        off_topic = (not result.get("on_topic", True)) and (
            result.get("topic_confidence", 0.0) >= self.cfg.max.topic_threshold
        )
        if off_topic:
            log.info(
                "Запрос отклонён LLM-фильтром (off-topic, topic_confidence=%.2f): %r",
                result.get("topic_confidence", 0.0), text[:60],
            )
            await self.max.send_message(MSG_OFF_TOPIC, chat_id=chat_id, user_id=user_id)
            return

        needs_operator = (not result["found_in_kb"]) or (
            result["confidence"] < self.cfg.max.confidence_threshold
        )
        log.info(
            "Решение по вопросу: needs_operator=%s (found_in_kb=%s confidence=%.2f "
            "threshold=%.2f) text=%r",
            needs_operator, result["found_in_kb"], result["confidence"],
            self.cfg.max.confidence_threshold, text[:60],
        )

        if needs_operator:
            # Телефон гражданина (если уже получен ботом) сохраняем в обращении —
            # он используется для связи с карточкой УСВО и истории (по MAX ID).
            urow = self.store.get_user(user_id)
            phone = (dict(urow).get("phone") or "") if urow else ""
            esc_id = self.store.create_escalation(user_id, chat_id, name, username, text, phone)
            await self._notify_operators_escalation(esc_id, name, username, text)
            await self.max.send_message(MSG_ESCALATED, chat_id=chat_id, user_id=user_id)
        else:
            await self.max.send_message(
                result["answer"], chat_id=chat_id, user_id=user_id,
                keyboard_rows=self._sm_entry_rows(),
            )

        # ИИ-подбор меры поддержки по тексту: если описанная ситуация соответствует
        # активной мере — предлагаем оформить её кнопкой (callback sm_pick).
        await self._maybe_offer_measure(chat_id, user_id, text)

        # Предложение записи: счётчик ведётся ОТ прошлого предложения и сбрасывается.
        if since_offer >= self.cfg.max.questions_before_offer:
            await self._offer_appointment(chat_id, user_id)
            self.store.reset_since_offer(user_id)

    async def _maybe_offer_measure(self, chat_id: str, user_id: str, text: str) -> None:
        """Предлагает подходящую меру поддержки, выбранную LLM по тексту запроса.

        Срабатывает только при настроенном Dify-ассистенте подбора
        (`dify.measure_app_key`): офлайн-матчер слишком груб для самостоятельных
        офферов и спамил бы кнопками. Источник мер — БД, без хардкода.
        """
        if not _dify_configured(self.cfg.dify.measure_app_key):
            return
        measures = measures_mod.active_measures(self.store)
        if not measures:
            return
        try:
            sel = await self.doc_ai.select_measure(text, measures, user_key=f"max-{user_id}")
        except Exception:  # noqa: BLE001
            log.exception("select_measure упало")
            return
        if not sel or not sel.get("found") or float(sel.get("confidence", 0.0) or 0.0) < 0.6:
            return
        measure = next((m for m in measures if m["id"] == sel.get("measure_id")), None)
        if not measure:
            return
        rows = [[{"text": f"Оформить: {measure['title']}",
                  "payload": json.dumps({"a": "sm_pick", "m": measure["id"]})}]]
        await self.max.send_message(
            f"Возможно, вам подойдёт мера поддержки: {measure['title']}. "
            f"Оформить заявление?",
            chat_id=chat_id, user_id=user_id, keyboard_rows=rows,
        )

    # ====================================================================
    # Ответ оператора
    # ====================================================================
    async def _handle_operator_message(
        self, operator_id: str, operator_name: str, text: str, reply_mid: str | None
    ) -> None:
        # 1) оператор нажал «Ответить» → состояние; 2) или ответил reply-ом на эскалацию.
        escalation_id = self.store.pop_operator_state(operator_id)
        source = "кнопка «Ответить»"
        if escalation_id is None and reply_mid:
            esc = self.store.get_open_escalation_by_mid(reply_mid)
            if esc:
                escalation_id = int(esc["id"])
                source = "reply на эскалацию"

        if escalation_id is None:
            log.info(
                "Сообщение в чате операторов без активной эскалации (operator=%s) — игнор.",
                operator_id,
            )
            return

        log.info("Ответ оператора на эскалацию #%s (%s)", escalation_id, source)
        await self._process_operator_answer(escalation_id, operator_id, operator_name, text)

    async def _process_operator_answer(
        self, escalation_id: int, operator_id: str, operator_name: str, text: str
    ) -> None:
        esc = self.store.get_escalation(escalation_id)
        if not esc:
            await self.max.send_message(
                f"Эскалация #{escalation_id} не найдена.", chat_id=self.operator_chat_id
            )
            return

        # 1) ответ пользователю
        await self.max.send_message(f"Ответ оператора:\n{text}", chat_id=esc["user_chat_id"])
        # 2) запись в выбранную базу знаний
        kb = await self.dify.add_to_kb(esc["question"], text)
        kb_status = _kb_status(kb)
        # 3) закрытие эскалации
        self.store.set_escalation_answer(escalation_id, text, operator_name or operator_id)
        self.store.close_escalation(escalation_id, operator_id)

        # 4) отчёт в чат операторов
        contact = (esc["user_name"] or "пользователь")
        if esc["username"]:
            contact += f" (@{esc['username']})"
        contact += f", id {esc['user_id']}"
        report = (
            f"✅ Ответ отправлен (эскалация #{escalation_id})\n\n"
            f"👤 Оператор: {operator_name or operator_id}\n"
            f"📨 Кому: {contact}\n"
            f"❓ Вопрос: {esc['question']}\n"
            f"💬 Ответ: {text}\n"
            f"📚 Запись в базу знаний: {kb_status}"
        )
        await self.max.send_message(report, chat_id=self.operator_chat_id)

    # ====================================================================
    # Callback-и от inline-кнопок
    # ====================================================================
    async def _handle_callback(self, update: dict) -> None:
        callback = update.get("callback") or {}
        callback_id = callback.get("callback_id")
        user = callback.get("user") or {}
        message = update.get("message") or {}
        recipient = message.get("recipient") or {}

        user_id = _id(user.get("user_id"))
        chat_id = _id(recipient.get("chat_id")) or user_id
        name = user.get("name") or user.get("first_name") or ""
        username = user.get("username") or ""
        source_mid = _msg_mid(message)  # сообщение, на котором была кнопка

        payload = _parse_payload(callback.get("payload"))
        action = payload.get("a")

        if action == "yes":  # «Записаться»
            await self.max.answer_callback(callback_id)
            await self._delete_if(source_mid)
            await self._send_slots(chat_id, user_id)
        elif action == "no":  # «Отказаться» → сообщение-оффер пропадает
            await self.max.answer_callback(callback_id, MSG_OFFER_DECLINED)
            await self._delete_if(source_mid)
        elif action == "slot":  # выбран слот → убираем список слотов
            await self.max.answer_callback(callback_id)
            await self._delete_if(source_mid)
            await self._handle_slot_selection(
                user_id, chat_id, name, username, payload.get("b", ""), payload.get("t", "")
            )
        elif action == "ans":  # оператор берёт эскалацию в ответ
            await self._claim_escalation(callback_id, user_id, name, int(payload.get("e", 0)))
        elif action == "doc_make":  # гражданин согласился оформить меру → заполняем заявление
            await self.max.answer_callback(callback_id)
            await self._delete_if(source_mid)
            await self._finalize_application(user_id, chat_id, int(payload.get("p", 0)))
        elif action == "doc_ok":  # гражданин подтвердил заполненное заявление → подаём
            await self.max.answer_callback(callback_id)
            await self._delete_if(source_mid)
            await self._submit_application(user_id, chat_id, name, username, int(payload.get("p", 0)))
        elif action == "doc_no":  # отказ → оффер просто исчезает, можно задавать вопросы дальше
            await self.max.answer_callback(callback_id)
            await self._delete_if(source_mid)
            await self._cancel_application(int(payload.get("p", 0)))
        elif action == "topic":  # выбор темы обращения на стартовом экране
            await self.max.answer_callback(callback_id)
            await self._delete_if(source_mid)
            await self._handle_topic(user_id, chat_id, name, username, payload.get("t", ""))
        elif action == "rel":  # выбор родства с участником СВО в гид-опросе
            await self.max.answer_callback(callback_id)
            await self._delete_if(source_mid)
            await self._handle_relation_choice(
                user_id, chat_id, name, username, payload.get("v", "")
            )
        elif action == "sub":  # подписка/отписка на рассылку (финал оформления меры)
            await self.max.answer_callback(callback_id)
            await self._delete_if(source_mid)
            await self._handle_subscription(
                user_id, chat_id,
                subscribed=bool(payload.get("v")),
                explicit_unsub=bool(payload.get("u")),
            )
        elif action == "sm_menu":  # «Оформление мер поддержки» → список активных мер
            await self.max.answer_callback(callback_id)
            await self._send_measure_menu(chat_id, user_id)
        elif action == "sm_pick":  # выбрана мера → запускаем сбор документов
            await self.max.answer_callback(callback_id)
            await self._delete_if(source_mid)
            await self._start_measure_flow(
                user_id, chat_id, name, username, int(payload.get("m", 0)),
                guided=bool(payload.get("g")),
            )
        elif action == "sm_ask":  # «Отмена (задать вопрос)» → выход к обычным вопросам
            await self.max.answer_callback(callback_id)
            await self._delete_if(source_mid)
            await self._cancel_to_question(user_id, chat_id)
        elif action == "sm_ok":  # гражданин подтвердил заявление меры → подаём
            await self.max.answer_callback(callback_id)
            await self._delete_if(source_mid)
            await self._submit_measure_application(
                user_id, chat_id, name, username, int(payload.get("p", 0))
            )
        elif action == "sm_redo":  # «Заполнить заново» → перезапуск сбора документов
            await self.max.answer_callback(callback_id)
            await self._delete_if(source_mid)
            await self._redo_measure_flow(user_id, chat_id, name, username, int(payload.get("p", 0)))
        elif action == "sm_cancel":  # «Отменить» → закрываем оформление меры
            await self.max.answer_callback(callback_id)
            await self._delete_if(source_mid)
            await self._cancel_measure_flow(chat_id, user_id, int(payload.get("p", 0)))
        else:
            await self.max.answer_callback(callback_id)

    async def _delete_if(self, message_id: str | None) -> None:
        if message_id:
            await self.max.delete_message(message_id)

    async def _claim_escalation(
        self, callback_id: str, operator_id: str, operator_name: str, escalation_id: int
    ) -> None:
        esc = self.store.get_escalation(escalation_id)
        if not esc or esc["status"] != "open":
            await self.max.answer_callback(callback_id, "Эскалация уже обработана.")
            return
        self.store.set_operator_state(operator_id, escalation_id)
        await self.max.answer_callback(callback_id, MSG_OPERATOR_PROMPT)
        await self.max.send_message(
            f"Оператор {operator_name or operator_id} отвечает на вопрос #{escalation_id}.\n"
            f"{MSG_OPERATOR_PROMPT}",
            chat_id=self.operator_chat_id,
        )

    # ====================================================================
    # Оформление меры поддержки по фотографиям документов
    # ====================================================================
    async def _handle_documents(
        self, user_id: str, chat_id: str, name: str, username: str, image_urls: list[str]
    ) -> None:
        """Шаг 1: ИИ читает текст документов по фото и подбирает меру поддержки.

        Текст с фотографий (визуальная модель) уходит в ассистент подбора меры
        (`select_measure` + база знаний мер). Подобранную меру (из заведённых
        администратором) предлагаем кнопкой «Оформить» (callback sm_pick) — дальше
        идёт обычный сценарий «Меры поддержки»: пошаговый сбор нужных документов.
        """
        self.store.ensure_user(user_id, chat_id, name, username)
        measures = measures_mod.active_measures(self.store)
        if not measures:
            await self.max.send_message(MSG_SM_NONE, chat_id=chat_id, user_id=user_id)
            return

        await self.max.send_message(MSG_DOC_ANALYZING, chat_id=chat_id, user_id=user_id)

        # Читаем документы визуальной моделью и подбираем меру по их содержанию.
        summary = await self.doc_ai.read_documents_text(image_urls, user_key=f"max-doc-{user_id}")
        try:
            sel = await self.doc_ai.select_measure(summary, measures, user_key=f"max-doc-{user_id}")
        except Exception:  # noqa: BLE001
            log.exception("select_measure по фото упало")
            sel = None

        measure = None
        if sel and sel.get("found") and float(sel.get("confidence", 0.0) or 0.0) >= 0.5:
            measure = next((m for m in measures if m["id"] == sel.get("measure_id")), None)

        if not measure:
            await self.max.send_message(
                MSG_DOC_NO_MEASURE, chat_id=chat_id, user_id=user_id,
                keyboard_rows=self._sm_entry_rows(),
            )
            return

        text = (
            "🔍 Готово! По вашим документам ИИ определил подходящую меру поддержки:\n\n"
            f"✅ {measure['title']}\n\n"
            "Оформить заявление? Я по очереди запрошу нужные документы."
        )
        rows = [[
            {"text": "Оформить", "payload": json.dumps({"a": "sm_pick", "m": measure["id"]})},
            {"text": "Отказаться", "payload": json.dumps({"a": "no"})},
        ]]
        await self.max.send_message(text, chat_id=chat_id, user_id=user_id, keyboard_rows=rows)

    async def _finalize_application(self, user_id: str, chat_id: str, application_id: int) -> None:
        """Шаг 2: ИИ заполняет заявление и присылает файл на подтверждение."""
        row = self.store.get_application(application_id)
        if not row:
            return
        try:
            extracted = json.loads(row["data"] or "{}")
        except Exception:  # noqa: BLE001
            extracted = {}

        # Поля заявления (для превью и хранения) — детерминированная нормализация.
        filled = normalize_application(extracted)
        self.store.update_application_data(application_id, json.dumps(filled, ensure_ascii=False))
        self.store.set_application_status(application_id, "awaiting_confirm")

        # Сам .docx формирует Dify-ассистент по DOCX-шаблону (узел generate_docx).
        # Если Dify не настроен/недоступен — собираем файл в коде из тех же полей.
        gen = await self.doc_ai.generate_application_docx(filled, user_key=f"max-doc-{user_id}")
        docx_bytes = gen["bytes"] if gen and gen.get("bytes") else build_application_docx(filled)
        caption = (
            "📄 Заявление заполнено. Проверьте данные и подтвердите отправку:\n\n"
            f"{application_summary(filled)}"
        )
        rows = [[
            {"text": "Подтвердить и подать", "payload": json.dumps({"a": "doc_ok", "p": application_id})},
            {"text": "Отказаться", "payload": json.dumps({"a": "doc_no", "p": application_id})},
        ]]
        sent = await self.max.send_document(
            docx_bytes, "Заявление.docx", chat_id=chat_id, user_id=user_id,
            caption=caption, keyboard_rows=rows,
        )
        if not sent.get("ok"):
            # Фолбэк, если отправка файла недоступна: текстовое превью + кнопки.
            await self.max.send_message(caption, chat_id=chat_id, user_id=user_id, keyboard_rows=rows)

    async def _submit_application(
        self, user_id: str, chat_id: str, name: str, username: str, application_id: int,
        user_files: list[str] | None = None,
    ) -> None:
        """Шаг 3: гражданин подтвердил → заявление подаётся в администрацию.

        Подтвердить можно двумя путями: кнопкой «Подтвердить и подать» (подаётся
        заполненный ботом .docx) ИЛИ прислав собственный файл вместо кнопки — тогда
        ссылки на присланные файлы лежат в user_files и подаются как само заявление.
        """
        row = self.store.get_application(application_id)
        if not row:
            return
        # Защита от повторной подачи (напр. остались старые кнопки под .docx, и
        # гражданин нажал «Подтвердить» уже после подачи своим файлом).
        if row["status"] == "submitted":
            return
        try:
            data = json.loads(row["data"] or "{}")
        except Exception:  # noqa: BLE001
            data = {}
        if user_files:
            # Файл(ы) гражданина становятся частью заявления.
            existing = data.get("user_files") or []
            data["user_files"] = existing + [u for u in user_files if u not in existing]
            self.store.update_application_data(application_id, json.dumps(data, ensure_ascii=False))

        self.store.set_application_status(application_id, "submitted")
        filled = normalize_application(data)

        if user_files:
            user_msg = (
                "✅ Спасибо, ваш файл получен. Заявление принято и направлено в "
                "администрацию Ленинского городского округа. Статус рассмотрения "
                "сообщим в этом чате."
            )
        else:
            user_msg = (
                "✅ Заявление принято и направлено в администрацию Ленинского городского "
                "округа. Статус рассмотрения сообщим в этом чате."
            )
        await self.max.send_message(user_msg, chat_id=chat_id, user_id=user_id)

        # Уведомление в чат операторов.
        contact = name or "гражданин"
        if username:
            contact += f" (@{username})"
        operator_text = (
            "🆕 Новое заявление на меру поддержки\n\n"
            f"Заявитель: {contact}\n"
            f"Мера: {row['measure_title']}\n"
            f"{application_summary(filled)}\n\n"
            f"Открыть в кабинете → раздел «Заявления» (#{application_id})."
        )
        if user_files:
            operator_text += "\n\n📎 Файл(ы) от заявителя:\n" + "\n".join(user_files)
        await self.max.send_message(operator_text, chat_id=self.operator_chat_id)

    async def _cancel_application(self, application_id: int) -> None:
        if not application_id:
            return
        row = self.store.get_application(application_id)
        # Не понижаем уже поданное заявление (старые кнопки «Отказаться» под .docx
        # не должны отменять заявление, которое гражданин уже подал своим файлом).
        if row and row["status"] == "submitted":
            return
        self.store.set_application_status(application_id, "rejected", "гражданин (отказ)")

    # ====================================================================
    # Сценарий «Меры поддержки»: выбор меры → пошаговый сбор документов →
    # распознавание → дозапрос полей → заявление → подтверждение → подача.
    # ====================================================================
    def _topic_rows(self) -> list[list[dict]]:
        """Три тематические кнопки стартового экрана (экран 1)."""
        return [
            [{"text": MSG_TOPIC_MEASURES,
              "payload": json.dumps({"a": "topic", "t": "measures"})}],
            [{"text": MSG_TOPIC_GKH, "payload": json.dumps({"a": "topic", "t": "gkh"})}],
            [{"text": MSG_TOPIC_ROADS, "payload": json.dumps({"a": "topic", "t": "roads"})}],
        ]

    def _sm_entry_rows(self) -> list[list[dict]]:
        """Inline-кнопка входа в сценарий подбора мер поддержки (гид-опрос)."""
        return [[{"text": MSG_TOPIC_MEASURES,
                  "payload": json.dumps({"a": "topic", "t": "measures"})}]]

    @staticmethod
    def _relation_rows() -> list[list[dict]]:
        """Кнопки быстрого выбора родства с участником СВО (экран 2).

        Первая кнопка — «Я участник» (сам участник СВО обращается за мерами),
        остальные — члены семьи.
        """
        return [
            [
                {"text": "Я участник", "payload": json.dumps({"a": "rel", "v": "Участник"})},
                {"text": "Супруга", "payload": json.dumps({"a": "rel", "v": "Супруга"})},
            ],
            [
                {"text": "Родитель", "payload": json.dumps({"a": "rel", "v": "Родитель"})},
                {"text": "Ребёнок", "payload": json.dumps({"a": "rel", "v": "Ребёнок"})},
            ],
        ]

    @staticmethod
    def _share_phone_rows() -> list[list[dict]]:
        """Кнопка «Поделиться номером телефона» (MAX request_contact, экран 3)."""
        return [[{"type": "request_contact", "text": MSG_SHARE_PHONE}]]

    # ====================================================================
    # Гид-опрос «Меры поддержки»: тема → родство/регион → сводка профиля →
    # телефон → выбор меры → (сбор документов) → регистрация + подписка.
    # ====================================================================
    async def _handle_topic(
        self, user_id: str, chat_id: str, name: str, username: str, topic: str
    ) -> None:
        """Экран 1: выбор темы обращения. Меры → гид-опрос; ЖКХ/Дороги — заглушка."""
        self.store.ensure_user(user_id, chat_id, name, username)
        if topic == "measures":
            await self._start_guided_flow(user_id, chat_id, name, username)
            return
        # «Вопросы ЖКХ» и «Дороги и благоустройство» — раздел в разработке.
        await self.max.send_message(
            MSG_TOPIC_STUB, chat_id=chat_id, user_id=user_id,
            keyboard_rows=self._sm_entry_rows(),
        )

    def _profile_from_db(self, user_id: str) -> dict:
        """Подтягивает сохранённый профиль анкеты (родство/регистрация/город/телефон).

        Возвращает dict в том же формате, что и state анкеты в bot_flows.data, —
        так его можно сразу отдать в `_advance_profile`/`_offer_measures_after_phone`.
        Пустые/неизвестные поля опускаются, чтобы анкета доспросила недостающее.
        """
        row = self.store.get_user(user_id)
        if row is None:
            return {}
        keys = set(row.keys())
        data: dict = {}
        relation = row["relation"] if "relation" in keys else None
        if relation:
            data["relation"] = relation
        region_ok = row["region_ok"] if "region_ok" in keys else None
        if region_ok is not None:
            data["region_ok"] = bool(region_ok)
        locality = row["locality"] if "locality" in keys else None
        if locality:
            data["locality"] = locality
        phone = row["phone"] if "phone" in keys else None
        if phone:
            data["phone"] = phone
        return data

    @staticmethod
    def _profile_complete(data: dict) -> bool:
        """Профиль собран полностью (родство + подтверждённая регистрация + телефон)."""
        return bool(data.get("relation")) and data.get("region_ok") is True \
            and bool(data.get("phone"))

    async def _start_guided_flow(
        self, user_id: str, chat_id: str, name: str, username: str
    ) -> None:
        """Экран 2: старт подбора мер.

        Вступительная анкета (родство/регистрация/телефон) спрашивается только при
        первом обращении. При повторном — профиль берётся из БД: полностью собранный
        подставляется без вопросов, частичный — доспрашивается только в недостающей части.
        """
        self.store.ensure_user(user_id, chat_id, name, username)
        data = self._profile_from_db(user_id)
        self.store.set_bot_flow(user_id, "collect_profile", _dump(data))

        # Профиль уже полностью собран ранее — подставляем и сразу к выбору меры.
        if self._profile_complete(data):
            region = "Московская область" + (
                f" (г.о. {data['locality']})" if data.get("locality") else ""
            )
            await self.max.send_message(
                MSG_PROFILE_KNOWN.format(
                    status=_status_label(data["relation"]),
                    region=region,
                    phone=data["phone"],
                ),
                chat_id=chat_id, user_id=user_id,
            )
            self.store.set_bot_flow(user_id, "choose_measure", _dump(data))
            await self._offer_measures_after_phone(user_id, chat_id, name, username, data)
            return

        # Первый контакт (родство ещё не известно) — полное вступление с двумя вопросами.
        if not data.get("relation"):
            await self.max.send_message(
                MSG_MEASURES_INTRO, chat_id=chat_id, user_id=user_id,
                keyboard_rows=self._relation_rows(),
            )
            return

        # Часть профиля уже известна — доспрашиваем только недостающее.
        await self._advance_profile(user_id, chat_id, name, username, data)

    async def _handle_relation_choice(
        self, user_id: str, chat_id: str, name: str, username: str, value: str
    ) -> None:
        """Нажата кнопка родства (экран 2). Сохраняем и двигаемся дальше по анкете."""
        self.store.ensure_user(user_id, chat_id, name, username)
        row = self.store.get_bot_flow(user_id)
        data = _load_bot_flow_data(row) if row else {}
        if value:
            data["relation"] = value
        await self._advance_profile(user_id, chat_id, name, username, data)

    async def _handle_guided_message(
        self, user_id: str, chat_id: str, name: str, username: str,
        row, image_urls: list[str], text: str, contact_phone: str,
    ) -> None:
        """Входящее сообщение во время гид-опроса (текст-ответы и присланный контакт)."""
        stage = row["stage"]
        data = _load_bot_flow_data(row)

        # Явная отмена на любом шаге опроса — возвращаемся к выбору темы.
        if text and _is_cancel(text) and not contact_phone:
            self.store.clear_bot_flow(user_id)
            await self.max.send_message(
                MSG_SM_CANCELLED, chat_id=chat_id, user_id=user_id,
                keyboard_rows=self._topic_rows(),
            )
            return

        if stage == "await_phone":
            phone = contact_phone or (_format_phone(text) if _looks_like_phone(text) else "")
            if not phone:
                await self.max.send_message(
                    MSG_NEED_PHONE, chat_id=chat_id, user_id=user_id,
                    keyboard_rows=self._share_phone_rows(),
                )
                return
            data["phone"] = phone
            self.store.set_user_phone(user_id, phone)
            await self._offer_measures_after_phone(user_id, chat_id, name, username, data)
            return

        if stage == "choose_measure":
            # Ждём нажатия кнопки выбора меры; на текст — повторяем список мер.
            await self._offer_measures_after_phone(user_id, chat_id, name, username, data)
            return

        # stage == "collect_profile": вытаскиваем родство и регион из текста.
        if text:
            if not data.get("relation"):
                rel = _parse_relation(text)
                if rel:
                    data["relation"] = rel
            if data.get("region_ok") is not True:
                reg = _parse_region(text)
                if reg is not None:
                    data["region_ok"], data["locality"] = reg
        await self._advance_profile(user_id, chat_id, name, username, data)

    async def _advance_profile(
        self, user_id: str, chat_id: str, name: str, username: str, data: dict
    ) -> None:
        """Двигает анкету: спрашивает недостающее (родство → регион) → сводка профиля.

        Каждое собранное поле сразу сохраняется в профиль пользователя (БД), чтобы
        при повторном обращении не переспрашивать (см. `_start_guided_flow`).
        """
        if not data.get("relation"):
            self.store.set_bot_flow(user_id, "collect_profile", _dump(data))
            await self.max.send_message(
                MSG_ASK_RELATION, chat_id=chat_id, user_id=user_id,
                keyboard_rows=self._relation_rows(),
            )
            return
        # Родство известно — сохраняем в профиль.
        self.store.set_user_profile(user_id, relation=data["relation"])
        region_ok = data.get("region_ok")
        if region_ok is not True:
            self.store.set_bot_flow(user_id, "collect_profile", _dump(data))
            # region_ok is False → житель другого региона; None → ещё не спрашивали.
            msg = MSG_REGION_OUTSIDE if region_ok is False else MSG_ASK_REGION
            await self.max.send_message(msg, chat_id=chat_id, user_id=user_id)
            return
        # Регистрация подтверждена — сохраняем регион/город в профиль.
        self.store.set_user_profile(
            user_id, region_ok=True, locality=data.get("locality") or ""
        )
        # Телефон уже известен из прошлого обращения — сразу к выбору меры.
        if data.get("phone"):
            self.store.set_bot_flow(user_id, "choose_measure", _dump(data))
            await self._offer_measures_after_phone(user_id, chat_id, name, username, data)
            return
        await self._send_profile_summary(user_id, chat_id, name, username, data)

    async def _send_profile_summary(
        self, user_id: str, chat_id: str, name: str, username: str, data: dict
    ) -> None:
        """Экран 3: сводка профиля с бейджами, список мер и запрос контакта."""
        self.store.set_bot_flow(user_id, "await_phone", _dump(data))
        measures = measures_mod.active_measures(self.store)
        relation = data.get("relation") or "Член семьи"
        locality = data.get("locality") or ""
        region_line = "Московская область" + (f" (г.о. {locality})" if locality else "")
        lines = [
            "Спасибо. Всё верно?",
            "",
            f"• Статус: {_status_label(relation)} 🟢 Подтверждено",
            f"• Регион: {region_line} 🟢 Подтверждено",
            "• Контактный телефон: +7 (___) ___-__-__ 🔴 Не заполнено",
            "",
        ]
        if measures:
            lines.append("Вам доступны меры поддержки:")
            lines += [f"• {m['title']}" for m in measures]
            lines.append("")
        lines.append(
            "Пожалуйста, нажмите кнопку ниже, чтобы поделиться контактом, и я помогу "
            "с оформлением документов."
        )
        await self.max.send_message(
            "\n".join(lines), chat_id=chat_id, user_id=user_id,
            keyboard_rows=self._share_phone_rows(),
        )

    async def _offer_measures_after_phone(
        self, user_id: str, chat_id: str, name: str, username: str, data: dict
    ) -> None:
        """После получения телефона: выбор конкретной меры (экран 4 — сбор документов)."""
        measures = measures_mod.active_measures(self.store)
        if not measures:
            self.store.clear_bot_flow(user_id)
            await self.max.send_message(MSG_SM_NONE, chat_id=chat_id, user_id=user_id)
            return
        if len(measures) == 1:
            # Единственная мера — сразу переходим к сбору документов.
            await self._start_measure_flow(
                user_id, chat_id, name, username, measures[0]["id"], guided=True
            )
            return
        self.store.set_bot_flow(user_id, "choose_measure", _dump(data))
        rows = [
            [{"text": m["title"],
              "payload": json.dumps({"a": "sm_pick", "m": m["id"], "g": 1})}]
            for m in measures
        ]
        # Внизу — выход из выбора меры в обычный режим вопросов.
        rows.append([{"text": MSG_MEASURE_CANCEL_ASK,
                      "payload": json.dumps({"a": "sm_ask"})}])
        await self.max.send_message(
            MSG_PICK_MEASURE, chat_id=chat_id, user_id=user_id, keyboard_rows=rows
        )

    async def _cancel_to_question(self, user_id: str, chat_id: str) -> None:
        """Кнопка «Отмена (задать вопрос)»: выход из выбора меры в режим вопросов.

        Сбрасываем состояние гид-опроса, чтобы следующее сообщение гражданина
        пошло обычным путём вопроса, а не как ответ анкеты выбора меры.
        """
        self.store.clear_bot_flow(user_id)
        await self.max.send_message(MSG_ASK_QUESTION, chat_id=chat_id, user_id=user_id)

    async def _send_documents_intro(self, chat_id: str, user_id: str, measure: dict) -> None:
        """Экран 4: перечисляет документы, которые нужно прислать по очереди."""
        docs = measure.get("documents") or []
        if not docs:
            return
        lines = [
            f"Отлично! Для оформления «{measure['title']}» мне понадобятся фотографии "
            "или PDF-сканы следующих документов:",
            "",
        ]
        lines += [f"{i}. {d['title']}." for i, d in enumerate(docs, 1)]
        lines += ["", "Вы можете прикрепить их по очереди прямо в этот чат."]
        await self.max.send_message("\n".join(lines), chat_id=chat_id, user_id=user_id)

    async def _finalize_guided_measure(
        self, application_id: int, chat_id: str, user_id: str, name: str, username: str,
        measure: dict, flow: dict,
    ) -> None:
        """Экран 5: заявка зарегистрирована + предложение подписки (гид-сценарий).

        В отличие от обычного пути мер (подтверждение .docx кнопкой), гид-сценарий из
        макета регистрирует заявку сразу после проверки документов и полей.
        """
        values = flow.get("extracted") or {}
        files = flow.get("collected_files") or []
        data = {
            "measure_id": measure["id"],
            "measure_title": measure["title"],
            "fields": values,
            "documents": [d["title"] for d in measure["documents"]],
            "user_files": files,
        }
        self.store.update_application_data(application_id, json.dumps(data, ensure_ascii=False))
        self.store.set_application_status(application_id, "submitted")
        self.store.clear_bot_flow(user_id)

        number = _application_number(application_id)
        rows = [
            [{"text": "🔔 Подписаться на рассылку", "payload": json.dumps({"a": "sub", "v": 1})}],
            [{"text": "🔕 Отписаться от рассылки",
              "payload": json.dumps({"a": "sub", "v": 0, "u": 1})}],
            [{"text": "❌ Нет, спасибо", "payload": json.dumps({"a": "sub", "v": 0})}],
        ]
        await self.max.send_message(
            MSG_FINAL_REGISTERED.format(number=number),
            chat_id=chat_id, user_id=user_id, keyboard_rows=rows,
        )
        # Уведомление в чат операторов.
        contact = name or "гражданин"
        if username:
            contact += f" (@{username})"
        labels = await self._ru_labels(measure)
        field_lines = "\n".join(
            f"  • {labels.get(k, k)}: {v}" for k, v in values.items() if v
        ) or "  (нет распознанных полей)"
        operator_text = (
            "🆕 Новое заявление на меру поддержки\n\n"
            f"Заявка: {number}\n"
            f"Заявитель: {contact}\n"
            f"Мера: {measure['title']}\n"
            f"Данные:\n{field_lines}\n\n"
            f"Открыть в кабинете → раздел «Заявления» (#{application_id})."
        )
        if files:
            operator_text += "\n\n📎 Документы заявителя:\n" + "\n".join(files)
        await self.max.send_message(operator_text, chat_id=self.operator_chat_id)

    def _subscription_rows(self, subscribed: bool) -> list[list[dict]]:
        """Кнопка-переключатель подписки на рассылку.

        Показывает действие, противоположное текущему состоянию: подписан → «Отписаться»,
        не подписан → «Подписаться». Благодаря ей выбор всегда обратим — гражданину не нужно
        заново проходить оформление меры, чтобы изменить подписку.
        """
        if subscribed:
            return [[{"text": "🔕 Отписаться от рассылки",
                      "payload": json.dumps({"a": "sub", "v": 0, "u": 1})}]]
        return [[{"text": "🔔 Подписаться на рассылку",
                  "payload": json.dumps({"a": "sub", "v": 1})}]]

    async def _handle_subscription(
        self, user_id: str, chat_id: str, subscribed: bool, explicit_unsub: bool = False
    ) -> None:
        """Обрабатывает подписку/отписку на рассылку (экран 5 и кнопка-переключатель).

        subscribed — новое состояние; explicit_unsub=True отличает явную отписку от «Нет,
        спасибо» (деклайн предложения), чтобы показать корректное подтверждение.
        """
        self.store.set_user_subscribed(user_id, subscribed)
        if subscribed:
            text = MSG_SUBSCRIBED
        elif explicit_unsub:
            text = MSG_UNSUBSCRIBED_DONE
        else:
            text = MSG_UNSUBSCRIBED
        rows = self._subscription_rows(subscribed) + self._topic_rows()
        await self.max.send_message(
            text, chat_id=chat_id, user_id=user_id, keyboard_rows=rows,
        )

    def _sm_cancel_rows(self, application_id: int) -> list[list[dict]]:
        """Inline-кнопка отмены оформления меры поддержки (на каждом шаге сбора)."""
        return [[{"text": "Отменить оформление",
                  "payload": json.dumps({"a": "sm_cancel", "p": application_id})}]]

    async def _ru_labels(self, measure: dict) -> dict[str, str]:
        """Карта {ключ: русская подпись} для плейсхолдеров меры.

        Подписи переводятся динамически (LLM-перевод `translate_labels` с офлайн-словарём
        как фолбэком), поэтому новые меры/поля тоже показываются гражданину по-русски, а
        не техническими латинскими именами плейсхолдеров. Уже кириллические подписи
        (заданные администратором) сохраняются как есть.
        """
        fields = measures_mod.placeholder_fields(measure)
        if not fields:
            return {}
        try:
            labels = await self.doc_ai.translate_labels(fields)
        except Exception:  # noqa: BLE001
            log.exception("translate_labels упало — использую исходные подписи")
            labels = [f["label"] for f in fields]
        return {f["key"]: (lbl or f["label"]) for f, lbl in zip(fields, labels)}

    def _load_measure(self, measure_id: int) -> dict | None:
        if not measure_id:
            return None
        row = self.store.get_support_measure(measure_id)
        return measures_mod.measure_to_dict(row) if row else None

    def _missing_labels(self, measure: dict, missing_keys: list[str]) -> list[str]:
        labels = {p["key"]: p["label"] for p in measure.get("placeholders", [])}
        return [labels.get(k, k) for k in missing_keys]

    async def _send_measure_menu(self, chat_id: str, user_id: str) -> None:
        """Шлёт список активных мер поддержки inline-кнопками (без хардкода — из БД)."""
        measures = measures_mod.active_measures(self.store)
        if not measures:
            await self.max.send_message(MSG_SM_NONE, chat_id=chat_id, user_id=user_id)
            return
        rows = [
            [{"text": m["title"], "payload": json.dumps({"a": "sm_pick", "m": m["id"]})}]
            for m in measures
        ]
        await self.max.send_message(MSG_SM_MENU, chat_id=chat_id, user_id=user_id, keyboard_rows=rows)

    async def _start_measure_flow(
        self, user_id: str, chat_id: str, name: str, username: str, measure_id: int,
        guided: bool = False,
    ) -> None:
        self.store.ensure_user(user_id, chat_id, name, username)
        # Выбор меры завершает гид-опрос: дальше работает состояние сбора документов.
        self.store.clear_bot_flow(user_id)
        measure = self._load_measure(measure_id)
        if not measure or not measure["active"]:
            await self.max.send_message(MSG_SM_UNAVAILABLE, chat_id=chat_id, user_id=user_id)
            return
        docs = [{"title": d["title"], "status": "pending", "urls": []}
                for d in measure["documents"]]
        flow = {
            "measure_id": measure_id, "doc_index": 0, "docs": docs,
            "collected_files": [], "extracted": {}, "missing": [],
            "stage": "waiting_document" if docs else "documents_uploaded",
            "request_mid": None,
            # guided=True → финал по макету (регистрация #СВО-… + подписка),
            # иначе — обычное подтверждение сформированного .docx кнопкой.
            "guided": guided,
        }
        application_id = self.store.create_measure_application(
            user_id, chat_id, name, username, measure_id, measure["title"],
            json.dumps(flow, ensure_ascii=False), status=flow["stage"],
        )
        if docs:
            # Экран 4: сначала перечисляем все нужные документы, затем запрашиваем первый.
            await self._send_documents_intro(chat_id, user_id, measure)
            await self._request_document(application_id, chat_id, user_id, flow, docs[0]["title"])
        else:
            await self._recognize_and_continue(
                application_id, chat_id, user_id, name, username, measure, flow
            )

    async def _request_document(
        self, application_id: int, chat_id: str, user_id: str, flow: dict, title: str
    ) -> None:
        res = await self.max.send_message(
            f"Отправьте фото документа: {title}", chat_id=chat_id, user_id=user_id,
            keyboard_rows=self._sm_cancel_rows(application_id),
        )
        flow["request_mid"] = _msg_mid(res)
        flow["stage"] = "waiting_document"
        self.store.update_application_flow(application_id, json.dumps(flow, ensure_ascii=False))
        self.store.set_application_status(application_id, "waiting_document")

    async def _handle_measure_flow_message(
        self, user_id: str, chat_id: str, name: str, username: str,
        row, image_urls: list[str], text: str,
    ) -> None:
        """Маршрутизирует входящее сообщение в активный сценарий «Меры поддержки»."""
        application_id = int(row["id"])
        flow = _load_flow(row)
        measure = self._load_measure(int(row["support_measure_id"] or 0))
        if not measure or not measure["active"]:
            await self.max.send_message(MSG_SM_UNAVAILABLE, chat_id=chat_id, user_id=user_id)
            self.store.set_application_status(application_id, "failed", "мера недоступна")
            return

        stage = flow.get("stage")
        if stage == "waiting_document":
            await self._collect_document(
                application_id, chat_id, user_id, name, username, measure, flow, image_urls, text
            )
        elif stage == "waiting_missing_fields":
            if not text:
                await self.max.send_message(MSG_SM_NEED_TEXT, chat_id=chat_id, user_id=user_id)
                return
            await self._apply_missing_fields(
                application_id, chat_id, user_id, name, username, measure, flow, text
            )
        elif stage == "ready_for_confirmation":
            await self.max.send_message(MSG_SM_NUDGE_CONFIRM, chat_id=chat_id, user_id=user_id)
        else:  # documents_uploaded / extracting_data — переходные стадии
            await self.max.send_message(MSG_SM_PROCESSING, chat_id=chat_id, user_id=user_id)

    async def _collect_document(
        self, application_id: int, chat_id: str, user_id: str, name: str, username: str,
        measure: dict, flow: dict, image_urls: list[str], text: str,
    ) -> None:
        if not image_urls:
            # Прислали не фото. Явная отмена — закрываем; иначе напоминаем (состояние
            # сбора НЕ сбрасываем).
            if text and _is_cancel(text):
                self.store.set_application_status(application_id, "cancelled", "гражданин (отмена)")
                await self.max.send_message(MSG_SM_CANCELLED, chat_id=chat_id, user_id=user_id)
                return
            await self.max.send_message(MSG_SM_NOT_PHOTO, chat_id=chat_id, user_id=user_id)
            return

        docs = flow.get("docs") or []
        idx = int(flow.get("doc_index", 0))
        if 0 <= idx < len(docs):
            docs[idx]["urls"] = (docs[idx].get("urls") or []) + image_urls
            docs[idx]["status"] = "uploaded"
        flow["collected_files"] = (flow.get("collected_files") or []) + image_urls
        # Сообщение-запрос по возможности скрываем (best-effort, как у офферов).
        await self._delete_if(flow.get("request_mid"))

        idx += 1
        flow["doc_index"] = idx
        if idx < len(docs):
            await self._request_document(application_id, chat_id, user_id, flow, docs[idx]["title"])
        else:
            flow["stage"] = "documents_uploaded"
            self.store.update_application_flow(application_id, json.dumps(flow, ensure_ascii=False))
            self.store.set_application_status(application_id, "documents_uploaded")
            await self._recognize_and_continue(
                application_id, chat_id, user_id, name, username, measure, flow
            )

    async def _recognize_and_continue(
        self, application_id: int, chat_id: str, user_id: str, name: str, username: str,
        measure: dict, flow: dict,
    ) -> None:
        await self.max.send_message(MSG_SM_ANALYZING, chat_id=chat_id, user_id=user_id)
        self.store.set_application_status(application_id, "extracting_data")
        fields = measures_mod.placeholder_fields(measure)
        keys = [f["key"] for f in fields]
        urls = flow.get("collected_files") or []
        try:
            res = await self.doc_ai.extract_fields(
                urls, fields, user_key=f"max-measure-{user_id}"
            )
        except Exception:  # noqa: BLE001
            log.exception("extract_fields упало — считаем все поля незаполненными")
            res = {"filled_fields": {}, "missing_fields": keys}

        extracted = dict(flow.get("extracted") or {})
        extracted.update(res.get("filled_fields") or {})
        flow["extracted"] = extracted
        check = check_required(keys, extracted)
        flow["missing"] = check["missing_fields"]
        self.store.update_application_flow(application_id, json.dumps(flow, ensure_ascii=False))

        if check["missing_fields"]:
            await self._ask_missing(application_id, chat_id, user_id, measure, flow)
        else:
            await self._generate_and_offer(
                application_id, chat_id, user_id, name, username, measure, flow
            )

    async def _ask_missing(
        self, application_id: int, chat_id: str, user_id: str, measure: dict, flow: dict
    ) -> None:
        flow["stage"] = "waiting_missing_fields"
        self.store.update_application_flow(application_id, json.dumps(flow, ensure_ascii=False))
        self.store.set_application_status(application_id, "waiting_missing_fields")
        missing = flow.get("missing") or []
        raw_labels = self._missing_labels(measure, missing)
        # Названия полей шаблона часто технические/латиницей — переводим их понятным
        # русским языком (LLM с офлайн-словарём-фолбэком), прежде чем просить гражданина.
        fields = [{"key": k, "label": lbl} for k, lbl in zip(missing, raw_labels)]
        try:
            labels = await self.doc_ai.translate_labels(fields)
        except Exception:  # noqa: BLE001
            log.exception("translate_labels упало — использую исходные подписи")
            labels = raw_labels
        lines = "\n".join(f"{i + 1}. {lbl}" for i, lbl in enumerate(labels))
        await self.max.send_message(
            f"{MSG_SM_MISSING_INTRO}\n\n{lines}", chat_id=chat_id, user_id=user_id,
            keyboard_rows=self._sm_cancel_rows(application_id),
        )

    async def _apply_missing_fields(
        self, application_id: int, chat_id: str, user_id: str, name: str, username: str,
        measure: dict, flow: dict, text: str,
    ) -> None:
        missing = list(flow.get("missing") or [])
        values = _split_user_values(text)
        extracted = dict(flow.get("extracted") or {})
        # Значения берём в том порядке, в котором их запрашивали.
        for i, key in enumerate(missing):
            if i < len(values) and values[i]:
                extracted[key] = values[i]
        flow["extracted"] = extracted
        keys = [f["key"] for f in measures_mod.placeholder_fields(measure)]
        check = check_required(keys, extracted)
        flow["missing"] = check["missing_fields"]
        self.store.update_application_flow(application_id, json.dumps(flow, ensure_ascii=False))

        if check["missing_fields"]:
            # Чего-то всё ещё не хватает — просим только оставшиеся поля.
            await self._ask_missing(application_id, chat_id, user_id, measure, flow)
        else:
            await self._generate_and_offer(
                application_id, chat_id, user_id, name, username, measure, flow
            )

    async def _generate_and_offer(
        self, application_id: int, chat_id: str, user_id: str, name: str, username: str,
        measure: dict, flow: dict,
    ) -> None:
        # Гид-сценарий из макета: без шага подтверждения .docx — сразу регистрация.
        if flow.get("guided"):
            await self._finalize_guided_measure(
                application_id, chat_id, user_id, name, username, measure, flow
            )
            return
        values = flow.get("extracted") or {}
        data = {
            "measure_id": measure["id"],
            "measure_title": measure["title"],
            "fields": values,
            "documents": [d["title"] for d in measure["documents"]],
            "user_files": flow.get("collected_files") or [],
        }
        self.store.update_application_data(application_id, json.dumps(data, ensure_ascii=False))
        flow["stage"] = "ready_for_confirmation"
        self.store.update_application_flow(application_id, json.dumps(flow, ensure_ascii=False))
        self.store.set_application_status(application_id, "ready_for_confirmation")

        rows = [
            [{"text": "Подтвердить", "payload": json.dumps({"a": "sm_ok", "p": application_id})}],
            [{"text": "Заполнить заново",
              "payload": json.dumps({"a": "sm_redo", "p": application_id})},
             {"text": "Отменить",
              "payload": json.dumps({"a": "sm_cancel", "p": application_id})}],
        ]
        # Подписи полей в превью — на русском (динамический перевод плейсхолдеров),
        # чтобы гражданин видел понятные названия, а не латинские имена плейсхолдеров.
        labels = await self._ru_labels(measure)
        caption = f"{MSG_SM_CONFIRM}\n\n{_measure_summary(measure, values, labels)}"

        gen = None
        try:
            gen = await self.doc_ai.generate_measure_docx(
                measure["template_path"], values, user_key=f"max-measure-{user_id}"
            )
        except Exception:  # noqa: BLE001
            log.exception("generate_measure_docx упало")

        if gen and gen.get("bytes"):
            sent = await self.max.send_document(
                gen["bytes"], "Заявление.docx", chat_id=chat_id, user_id=user_id,
                caption=caption, keyboard_rows=rows,
            )
            if not sent.get("ok"):
                await self.max.send_message(
                    caption, chat_id=chat_id, user_id=user_id, keyboard_rows=rows
                )
        else:
            await self.max.send_message(
                f"{caption}\n\n{MSG_SM_NO_TEMPLATE}",
                chat_id=chat_id, user_id=user_id, keyboard_rows=rows,
            )

    async def _submit_measure_application(
        self, user_id: str, chat_id: str, name: str, username: str, application_id: int
    ) -> None:
        row = self.store.get_application(application_id)
        if not row or row["status"] == "submitted":
            return
        try:
            data = json.loads(row["data"] or "{}")
        except Exception:  # noqa: BLE001
            data = {}

        self.store.set_application_status(application_id, "submitted")
        await self.max.send_message(MSG_SM_SENT, chat_id=chat_id, user_id=user_id)

        # Уведомление в чат операторов (как для существующих заявлений).
        contact = name or "гражданин"
        if username:
            contact += f" (@{username})"
        fields = data.get("fields") or {}
        measure = self._load_measure(int(row["support_measure_id"] or 0))
        labels = await self._ru_labels(measure) if measure else {}
        field_lines = "\n".join(
            f"  • {labels.get(k, k)}: {v}" for k, v in fields.items() if v
        ) or "  (нет распознанных полей)"
        files = data.get("user_files") or []
        operator_text = (
            "🆕 Новое заявление на меру поддержки\n\n"
            f"Заявитель: {contact}\n"
            f"Мера: {row['measure_title']}\n"
            f"Данные:\n{field_lines}\n\n"
            f"Открыть в кабинете → раздел «Заявления» (#{application_id})."
        )
        if files:
            operator_text += "\n\n📎 Документы заявителя:\n" + "\n".join(files)
        await self.max.send_message(operator_text, chat_id=self.operator_chat_id)

    async def _redo_measure_flow(
        self, user_id: str, chat_id: str, name: str, username: str, application_id: int
    ) -> None:
        row = self.store.get_application(application_id)
        if not row or row["status"] == "submitted":
            return
        measure = self._load_measure(int(row["support_measure_id"] or 0))
        if not measure or not measure["active"]:
            await self.max.send_message(MSG_SM_UNAVAILABLE, chat_id=chat_id, user_id=user_id)
            self.store.set_application_status(application_id, "failed", "мера недоступна")
            return
        # Закрываем текущую заявку и стартуем сбор заново (новая заявка станет активной).
        self.store.set_application_status(application_id, "cancelled", "перезаполнение")
        await self._start_measure_flow(user_id, chat_id, name, username, measure["id"])

    async def _cancel_measure_flow(
        self, chat_id: str, user_id: str, application_id: int
    ) -> None:
        if not application_id:
            return
        row = self.store.get_application(application_id)
        if row and row["status"] == "submitted":
            return
        self.store.set_application_status(application_id, "cancelled", "гражданин (отмена)")
        await self.max.send_message(MSG_SM_CANCELLED, chat_id=chat_id, user_id=user_id)

    # ====================================================================
    # Запись на приём
    # ====================================================================
    async def _offer_appointment(self, chat_id: str, user_id: str = "") -> None:
        rows = [[
            {"text": "Записаться", "payload": json.dumps({"a": "yes"})},
            {"text": "Отказаться", "payload": json.dumps({"a": "no"})},
        ]]
        await self.max.send_message(MSG_OFFER, chat_id=chat_id, user_id=user_id, keyboard_rows=rows)

    async def _send_slots(self, chat_id: str, user_id: str = "") -> None:
        slots = iter_slots(self.cfg.schedule_file)
        if not slots:
            await self.max.send_message(
                "Свободных слотов пока нет. Попробуйте позже.", chat_id=chat_id, user_id=user_id
            )
            return

        rows: list[list[dict]] = []
        for s in slots:
            payload = json.dumps({"a": "slot", "b": s.building_id, "t": s.time})
            rows.append([{"text": f"{s.building_short} · {s.time}", "payload": payload}])

        await self.max.send_message(MSG_PICK_TIME, chat_id=chat_id, user_id=user_id, keyboard_rows=rows)

    async def _handle_slot_selection(
        self, user_id: str, chat_id: str, name: str, username: str,
        building_id: str, time_str: str,
    ) -> None:
        self.store.ensure_user(user_id, chat_id, name, username)
        building = find_building(self.cfg.schedule_file, building_id) or {}
        building_name = building.get("name", building_id)
        self.store.create_appointment(user_id, building_id, building_name, time_str)

        last3 = self.store.last_questions(user_id, 3)
        history = "\n".join(f"  {i + 1}. {q}" for i, q in enumerate(last3)) or "  (нет вопросов)"
        contact = f"{name}".strip() or user_id
        if username:
            contact += f" (@{username})"

        operator_text = (
            "🗓 Новая заявка на приём\n\n"
            f"Контакты: {contact}\n"
            f"ID пользователя: {user_id}\n"
            f"Здание: {building_name}\n"
            f"Адрес: {building.get('address', '')}\n"
            f"Время: {time_str}\n\n"
            f"Последние вопросы пользователя:\n{history}"
        )
        await self.max.send_message(operator_text, chat_id=self.operator_chat_id)
        await self.max.send_message(
            f"{MSG_APPT_CONFIRMED}\n\n{building_name}\n{building.get('address', '')}\nВремя: {time_str}",
            chat_id=chat_id, user_id=user_id,
        )

    # ====================================================================
    # Эскалация в чат операторов
    # ====================================================================
    async def _notify_operators_escalation(
        self, escalation_id: int, name: str, username: str, question: str
    ) -> None:
        contact = name or "пользователь"
        if username:
            contact += f" (@{username})"
        text = (
            f"❓ Вопрос требует оператора (#{escalation_id})\n\n"
            f"От: {contact}\n"
            f"Вопрос: {question}\n\n"
            f"Нажмите «Ответить» или ответьте reply-ом на это сообщение."
        )
        rows = [[{"text": "Ответить", "payload": json.dumps({"a": "ans", "e": escalation_id})}]]
        res = await self.max.send_message(text, chat_id=self.operator_chat_id, keyboard_rows=rows)
        # Запоминаем id сообщения-эскалации, чтобы принять ответ через reply.
        mid = _msg_mid(res)
        if mid:
            self.store.set_escalation_mid(escalation_id, mid)

    def _sla_business_days(self) -> int:
        """Регламент ответа (календарных дней, все дни подряд): значение из
        «Настроек» кабинета (app_settings) приоритетнее дефолта YAML, чтобы
        напоминания совпадали с тем, что видит оператор в кабинете."""
        default = int(self.cfg.web.sla_business_days)
        getter = getattr(self.store, "get_setting", None)
        if getter is None:
            return default
        raw = getter("sla_business_days")
        if raw is None:
            return default
        try:
            return max(1, min(30, int(raw)))
        except (TypeError, ValueError):
            return default

    async def remind_overdue_escalations(self) -> int:
        """Повторно уведомляет операторов о просроченных (по SLA) обращениях.

        Регламент — заданный админом в «Настройках» (app_settings), иначе дефолт
        `web.sla_business_days` календарных дней. Каждое обращение напоминается один раз
        (после отметки `sla_notified_at`), чтобы не спамить чат операторов.
        Возвращает число отправленных напоминаний. Best-effort — ошибка отправки одному
        обращению не мешает остальным."""
        days = self._sla_business_days()
        sent = 0
        for row in self.store.list_open_escalations():
            d = dict(row)
            if d.get("sla_notified_at"):
                continue
            sla = sla_fields(d.get("created_at") or 0, business_days=days,
                             status=d.get("status") or "open")
            if not sla["is_overdue"]:
                continue
            contact = d.get("user_name") or "пользователь"
            if d.get("username"):
                contact += f" (@{d['username']})"
            text = (
                f"⏰ Просрочено обращение (#{d['id']})\n\n"
                f"От: {contact}\n"
                f"Возраст: {sla['age']} · регламент {days} дн.\n"
                f"Вопрос: {d.get('question') or ''}\n\n"
                f"Ответьте «Ответить» или reply-ом на это сообщение."
            )
            rows = [[{"text": "Ответить", "payload": json.dumps({"a": "ans", "e": d["id"]})}]]
            try:
                res = await self.max.send_message(
                    text, chat_id=self.operator_chat_id, keyboard_rows=rows
                )
            except Exception:  # noqa: BLE001
                log.exception("Напоминание о просрочке #%s не отправлено", d["id"])
                continue
            if isinstance(res, dict) and res.get("ok") is False:
                continue
            mid = _msg_mid(res)
            if mid:
                self.store.set_escalation_mid(int(d["id"]), mid)
            self.store.set_escalation_sla_notified(int(d["id"]))
            self.store.add_escalation_event(
                int(d["id"]), "sla_reminder", "Напоминание операторам о просрочке SLA"
            )
            sent += 1
        if sent:
            log.info("SLA-напоминания: отправлено %d просроченных обращений.", sent)
        return sent


def _id(value) -> str:
    return "" if value is None else str(value)


def _kb_status(kb) -> str:
    """Человекочитаемый статус записи ответа в базу знаний (Dify Dataset API)."""
    if not isinstance(kb, dict):
        return "ошибка — см. логи сервиса"
    if kb.get("skipped"):
        return "пропущена (датасет не настроен)"
    # Успех Dify create-by-text: возвращает document/batch (или id).
    if kb.get("document") or kb.get("batch") or kb.get("id"):
        return "да"
    return "ошибка — см. логи сервиса"


def _msg_mid(obj) -> str | None:
    """Достаёт id сообщения (mid). Принимает и объект сообщения, и ответ MAX на отправку."""
    if not isinstance(obj, dict):
        return None
    m = obj.get("message", obj)
    if isinstance(m, dict):
        body = m.get("body") or {}
        return body.get("mid") or m.get("mid")
    return None


def _reply_mid(message: dict) -> str | None:
    """id сообщения, на которое отвечают reply-ом (если это reply)."""
    link = message.get("link") or {}
    if isinstance(link, dict):
        body = link.get("message") or {}
        return link.get("mid") or (body.get("mid") if isinstance(body, dict) else None)
    return None


def _collect_attachment_urls(message: dict, types: tuple[str, ...]) -> list[str]:
    """Извлекает URL вложений сообщения MAX заданных типов.

    MAX кладёт вложения в body.attachments; ссылка может лежать в payload.url
    или payload.photos[*].url (формат немного разнится по версиям).
    """
    body = message.get("body") or message
    attachments = body.get("attachments") or message.get("attachments") or []
    urls: list[str] = []
    for att in attachments:
        if not isinstance(att, dict):
            continue
        if att.get("type") not in types:
            continue
        payload = att.get("payload") or {}
        url = payload.get("url")
        if url:
            urls.append(url)
            continue
        photos = payload.get("photos")
        if isinstance(photos, dict):
            for ph in photos.values():
                if isinstance(ph, dict) and ph.get("url"):
                    urls.append(ph["url"])
        elif isinstance(photos, list):
            for ph in photos:
                if isinstance(ph, dict) and ph.get("url"):
                    urls.append(ph["url"])
    return urls


def _image_urls(message: dict) -> list[str]:
    """URL фотографий (для анализа документов визуальной моделью)."""
    return _collect_attachment_urls(message, ("image", "photo"))


def _attachment_urls(message: dict) -> list[str]:
    """URL любых присланных файлов (фото И документы/видео/аудио).

    Нужно для сценария «гражданин прислал свой файл вместо кнопки подтверждения»:
    заявлением считается всё, что он приложил, а не только фотографии.
    """
    return _collect_attachment_urls(
        message, ("image", "photo", "file", "document", "video", "audio")
    )


def _parse_payload(raw) -> dict:
    if isinstance(raw, dict):
        return raw
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
        return parsed if isinstance(parsed, dict) else {}
    except Exception:
        return {}


def _load_flow(row) -> dict:
    """Безопасно разбирает JSON-состояние сценария «Меры поддержки» из строки заявки."""
    try:
        return json.loads(row["flow"] or "{}")
    except Exception:  # noqa: BLE001
        return {}


def _is_sm_trigger(text: str) -> bool:
    """Текстовый триггер входа в сценарий «Меры поддержки» (вместо inline-кнопки)."""
    t = (text or "").strip().lower()
    if not t:
        return False
    if t == MSG_SM_BUTTON.lower():
        return True
    return "оформ" in t and "поддержк" in t


def _is_cancel(text: str) -> bool:
    t = (text or "").strip().lower()
    return any(w in t for w in _CANCEL_WORDS)


def _split_user_values(text: str) -> list[str]:
    """Разбивает текст пользователя на значения для незаполненных полей.

    Значения берутся построчно (нумерация/маркеры списка срезаются) в том порядке,
    в котором поля запрашивались.
    """
    out: list[str] = []
    for line in (text or "").splitlines():
        line = re.sub(r"^\s*\d+[.)]\s*", "", line)
        line = re.sub(r"^\s*[-•·]\s*", "", line).strip()
        if line:
            out.append(line)
    return out


def _measure_summary(measure: dict, values: dict, labels: dict | None = None) -> str:
    """Короткое превью заявления меры поддержки для сообщения в MAX.

    `labels` — карта {ключ: русская подпись} (см. MaxBot._ru_labels); если её нет,
    берётся исходная подпись плейсхолдера.
    """
    labels = labels or {}
    lines = [f"📄 {measure['title']}"]
    for p in measure.get("placeholders", []):
        val = values.get(p["key"])
        if val:
            lines.append(f"{labels.get(p['key']) or p['label']}: {val}")
    return "\n".join(lines)


# ---- гид-опрос «Меры поддержки»: разбор ответов и вспомогательное -----------

def _dump(data: dict) -> str:
    return json.dumps(data, ensure_ascii=False)


def _load_bot_flow_data(row) -> dict:
    """Безопасно разбирает JSON-состояние анкеты «Меры поддержки» (bot_flows.data)."""
    if not row:
        return {}
    try:
        d = json.loads(row["data"] or "{}")
        return d if isinstance(d, dict) else {}
    except Exception:  # noqa: BLE001
        return {}


def _application_number(application_id: int) -> str:
    """Человеко-читаемый номер заявки в формате из макета (#СВО-…)."""
    return f"#СВО-{99280 + int(application_id)}"


def _parse_relation(text: str) -> str:
    """Определяет родство с участником СВО по свободному тексту гражданина.

    Родство членов семьи проверяется раньше «сам участник», чтобы фраза вида
    «супруга участника СВО» распознавалась как «Супруга», а не как «Участник».
    """
    t = (text or "").lower()
    if re.search(r"супруг|жена|замуж|\bмуж\b", t):
        return "Супруга"
    if re.search(r"родител|мать|мама|отец|папа", t):
        return "Родитель"
    if re.search(r"ребён|ребен|сын|доч|\bдет", t):
        return "Ребёнок"
    if re.search(r"\bя\s+сам|я\s+участник|участник\s+сво|\bя\s+боец|военнослуж|мобилизов|\bя\s+сво\b", t):
        return "Участник"
    return ""


def _status_label(relation: str) -> str:
    """Строка статуса для сводки профиля (учёт «сам участник СВО»)."""
    if (relation or "").strip().lower().startswith("участник"):
        return "Участник СВО"
    return f"{relation} участника СВО"


# Известные населённые пункты Ленинского ГО (по корню слова → именительный падеж),
# чтобы «прописана в Видном» → «Видное». Для прочих берём слово после предлога «в».
_LOCALITY_STEMS: list[tuple[str, str]] = [
    ("видн", "Видное"),
    ("ленинск", "Ленинский"),
    ("развилк", "Развилка"),
    ("горк", "Горки Ленинские"),
    ("бутов", "Бутово"),
    ("москв", "Москва"),
]


def _extract_locality(text: str) -> str:
    t = (text or "").lower()
    for stem, name in _LOCALITY_STEMS:
        if stem in t:
            return name
    m = re.search(r"\bв\s+([а-яё\-]{3,})", t)
    if m:
        w = m.group(1)
        return w[:1].upper() + w[1:]
    return ""


def _parse_region(text: str):
    """Разбирает ответ о регистрации в Московской области.

    Возвращает (True, город) — подтверждено; (False, "") — другой регион; None —
    из текста ничего про регион понять не удалось (нужно переспросить).
    """
    t = (text or "").lower()
    if not t:
        return None
    if re.search(r"\bнет\b|не заре|не пропис|друг(ой|ом)\s+регион|не\s+в\s+московск|"
                 r"не\s+в\s+подмоск|не\s+москов", t):
        return (False, "")
    locality = _extract_locality(text)
    if locality or re.search(
        r"московск|подмоск|моск\.?\s*обл|\bмо\b|\bда\b|прописан|зарегистр|проживаю|живу", t
    ):
        return (True, locality)
    return None


def _looks_like_phone(text: str) -> bool:
    return len(re.sub(r"\D", "", text or "")) >= 10


def _format_phone(raw: str) -> str:
    """Приводит номер к виду +7 (XXX) XXX-XX-XX (если распознаётся 10/11 цифр)."""
    digits = re.sub(r"\D", "", raw or "")
    if len(digits) == 11 and digits[0] in "78":
        digits = digits[1:]
    if len(digits) == 10:
        return f"+7 ({digits[0:3]}) {digits[3:6]}-{digits[6:8]}-{digits[8:10]}"
    return (raw or "").strip()


def _contact_phone(message: dict) -> str:
    """Достаёт телефон из вложения-контакта MAX (кнопка «Поделиться номером»)."""
    body = message.get("body") or message
    attachments = body.get("attachments") or message.get("attachments") or []
    for att in attachments:
        if not isinstance(att, dict) or att.get("type") != "contact":
            continue
        payload = att.get("payload") or {}
        for key in ("tel", "phone", "vcfPhone"):
            val = payload.get(key)
            if val:
                return _format_phone(str(val))
        vcf = str(payload.get("vcfInfo") or "")
        m = re.search(r"TEL[^:]*:\s*([+\d][\d\s\-()]{6,})", vcf)
        if m:
            return _format_phone(m.group(1))
        # На всякий случай — любой телефоноподобный фрагмент во вложении.
        m = re.search(r"\+?\d[\d\s\-()]{8,}\d", json.dumps(payload, ensure_ascii=False))
        if m:
            return _format_phone(m.group(0))
    return ""
