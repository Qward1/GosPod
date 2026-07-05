# AGENTS.md

This file provides guidance to Codex (Codex.ai/code) when working with code in this repository.

## Что это

Проект «Господдержка СВО» — два связанных артефакта:

1. **`assistant.yml`** — экспорт Dify-приложения (`advanced-chat`), оркестрация всех сценариев.
   Импортируется в Dify, не запускается локально.
2. **`microservice/`** — FastAPI-сервис на Python. Делает две вещи: (а) извлекает данные из Excel
   для задания 1; (б) содержит **всю stateful-логику MAX-бота** (задание 2) — Dify не умеет вести
   диалог с callback-ами, счётчиками и операторами.

Исходные файлы пользователя (`Информация для базы знаний.txt`, `Spravka_Shablon (1).docx`,
`USVO_*.xlsx`, `Ассистент Цифровой Офис.yml`) — это входные данные/референс, не код.

## Команды (выполнять из `microservice/`)

```bash
pip install -r requirements.txt
uvicorn app.main:app --host 0.0.0.0 --port 8080      # запуск сервиса

# Настройка/диагностика MAX-бота (нужен заполненный config.yaml):
python maxctl.py me                  # проверить токен (GET /me)
python maxctl.py subscriptions       # текущие подписки на вебхук
python maxctl.py subscribe <https-url>/max/webhook
python maxctl.py unsubscribe <https-url>/max/webhook
python maxctl.py poll                # long polling (для localhost без публичного HTTPS)
```

Линтера/форматтера нет. Для новых модулей есть pytest-тесты в `microservice/tests/`.
Проверка делается так:
```bash
python -m compileall -q app maxctl.py          # синтаксис
python -m pytest tests -q                      # детерминированные тесты без сети
# API-поведение также проверяется через fastapi.testclient.TestClient и фейковые
# MaxClient/DifyClient/DocAI.
```

## Архитектура: Dify (`assistant.yml`)

Граф — дерево с **роутером на старте**. В `Start` есть текстовый вход `channel`:
- `channel == "max"` → ветка MAX: `knowledge-retrieval` → LLM (structured_output) → code `max_pack`
  → answer. **Ответ — это JSON-строка** `{answer, found_in_kb, confidence}`, которую парсит
  микросервис. Ветка MAX **физически не связана рёбрами** с заданием 1 — это и есть требуемая изоляция.
- иначе → задание 1: `http-request` (в микросервис `/excel/extract`) → code `parse_excel`
  → `question-classifier` (2 класса) → «Вопрос по таблице» (LLM→answer) или «Создание справки»
  (LLM→code `build_docx`→tool `generate_docx`→answer с файлом).

При правках графа держать в голове: модель `qwen3-32b-fp8-v2` (`langgenius/openai_api_compatible`),
лимит входа **32k токенов**; DOCX через плагин `scibox/document-generator`; узлы `dataset_ids` и URL
микросервиса — плейсхолдеры (`REPLACE_WITH_KB_DATASET_ID`, `CHANGE-ME-microservice`), их выбирают/
правят после импорта. Шаблон справки использует фиксированный набор плейсхолдеров: `approver_position`,
`approver_name`, `reference_title`, `task_title`, `reference_date`, `protocol_number`,
`tasks_completed` — `build_docx` обязан отдавать ровно их.

## Архитектура: микросервис (`microservice/app/`)

- `config.py` — конфиг грузится из **YAML, не из .env** (на сервере `.env` запрещён). Путь:
  `CONFIG_PATH` или `./config.yaml`. `get_config()` кэширует.
- `excel/extractor.py` — ключевая логика влезания в 32k: длинные названия столбцов выносятся в
  **легенду один раз**, в записях идут индексы `[N]`, пустые ячейки выбрасываются. Бюджет
  `excel.max_chars` (60000 ≈ 30k токенов) с флагом `truncated`. `/excel/extract` принимает файл
  тремя способами (multipart `file` / `file_url` / raw body) — нарочно, чтобы не зависеть от версии Dify.
- `max/` — бот: `router.py` (вебхук) → `bot_logic.py` (вся логика) → `client.py` (MAX API),
  `dify_client.py` (вызов MAX-ветки Dify + запись в БЗ через Dataset API), `store.py` (SQLite-состояние),
  `schedule.py` (читает `data/schedule.yaml` на каждый запрос), `polling.py` (общий цикл long polling).
- `main.py` — lifespan создаёт `Store`, `MaxClient`, `DifyClient`, `DocAI`, `AuthService`, `MaxBot`
  в `app.state`; при `max.polling=true` поднимает фоновый `poll_loop`.
- `auth/` — авторизация веб-кабинета в стиле ЕСИА (Ory Kratos): `service.py` (demo/kratos + HMAC-
  cookie), `router.py` (`/api/web/auth/*`), `deps.py` (`require_user` защищает весь `/api/web/*`).
  Demo-режим без Kratos; реальный Kratos — `deploy/kratos/`.
- `docs/` — сценарий «мера поддержки по фото»: `forms.py` (схема заявления ЖКУ + сборка .docx),
  `doc_ai.py` (визуальная модель `doc_vision_app_key` + ассистент `doc_fill_app_key`, офлайн-фолбэк).
  Промты — `PROMPTS.md`; Dify-ассистенты — `vision_assistant.yml` (VL, шаг ①) и
  `document_assistant.yml` (заполнение + generate_docx, шаг ②). MAX-клиент при 400 по
  `chat_id` повторяет по `user_id` (личный диалог).
- MAX-бот ведёт flow по фото: `_handle_documents` → callback `doc_make` (`_finalize_application`) →
  callback `doc_ok` (`_submit_application`). Заявления — таблица `applications`, видны в админ-
  разделе «Заявления». Веб-кабинет: разделы «Обращения», «Карточки УСВО», «Заявления»,
  «Аналитика» и админские «Настройки», тепловая карта Ленинского ГО,
  строка «Поручение Главы округа» над частью карточек УСВО.
- **Меры поддержки** (управляемые админом, без хардкода): таблица `support_measures` (`store.py`);
  CRUD `web/measures_router.py` (`/api/web/settings/support-measures/*`, `require_admin`) +
  подраздел «Меры поддержки» внутри «Заявлений» (`app.js`). Бот: кнопка/команда «Оформление мер
  поддержки» → меню активных мер (`sm_pick`) → пошаговый сбор фото (`flow` в `applications`,
  гейт `get_active_flow`) → `DocAI.extract_fields` → `templates.check_required`/дозапрос полей →
  `templates.fill_template_docx` по шаблону меры → `sm_ok` (`_submit_measure_application`).
  LLM-подбор меры — `DocAI.select_measure` (`measure_assistant.yml` / офлайн `measures.match_measure_offline`).
  Helpers — `app/docs/measures.py`, `app/docs/templates.py`. KB-синхронизация — `WebService.sync_measures_kb`.
- **Карточки УСВО имеют два источника**: `UsvoStore` читает основную Excel-таблицу и перечитывает
  её по `mtime`; `UsvoCardStore` читает загруженные/отредактированные карточки из SQLite
  `usvo_cards`. `WebService._all_records()` объединяет их и скрывает табличную карточку, если в БД
  есть оверрайд с той же идентичностью. Публичный ID SQLite-карточки:
  `USVO_DB_ID_BASE + rowid`, где `USVO_DB_ID_BASE = 100000`.
- Реальная схема карточки — `UsvoRecord`: шапка `id/name/status/call_date/birth_date/phone/address/
  awards`, списки полей `primary/secondary/extra`, аналитические `flags`, `head_directive`,
  `source`, `history/history_raw`. Для произвольных загруженных Excel используются все непустые
  пары `label/value`; нельзя ограничивать новые интеграции только фиксированными 34 колонками.
- CRUD карточек находится в `WebService.import_usvo/update_usvo/delete_usvo/clear_uploaded_usvo`
  и маршрутах `/api/web/usvo*`. Редактирование табличной карточки создаёт SQLite-оверрайд с новым
  публичным ID; удалять напрямую можно только загруженные карточки.
- Веб-SPA не имеет сборщика: `web/static/index.html`, `app.js`, `styles.css`. Все обычные разделы
  доступны любой авторизованной роли; только «Настройки» и отдельные admin-router-ы скрыты/
  защищены через `require_admin`. Пользователь идентифицируется стабильным `user["sub"]`, а не
  отображаемым именем.
- `web.ai` — существующий провайдер черновиков (`local|dify|openai_compatible`), отдельный от
  MAX-ветки. Секреты проекта задаются только через `config.yaml`/`CONFIG_PATH`; `.env` на сервере
  не используется. Для новых Dify-приложений и датасетов добавлять отдельные поля конфига, не
  переиспользовать ключи неявно и не хардкодить ID.
- **«Чат с ИИ»** — `web/ai_chat.py` + `web/ai_chat_router.py`, доступен всем авторизованным
  ролям. Диалоги и сообщения хранятся в `ai_chats`/`ai_chat_messages`; владелец всегда
  `user["sub"]`. API: `/api/web/ai-chats*`. Форматы: `text`, `detailed_reference`,
  `brief_reference`. Отдельное Dify-приложение (Chat/Chatflow) для ответов —
  готовый экспорт `usvo_chat_assistant.yml` в корне (Start → knowledge-retrieval по
  датасету карточек → LLM → answer); `ai_chat.py` собирает весь контекст в `query`
  и зовёт `DifyClient.ask_text`. Описание/ручная сборка — `microservice/USVO_CHAT_PROMPT.md`.
- **Точный поиск по карточкам (Text-to-SQL, не RAG)** — `web/usvo_index.py` + `web/usvo_query.py`.
  Для счётных/составных вопросов («сколько с >3 детьми», «инвалидность 2 группы и двое детей») чат
  не отдаёт 1500 карточек в контекст, а: вопрос → JSON-фильтр (DSL) → `validate_filter` (whitelist
  полей/операторов, только bind-параметры) → запрос к производному SQLite `data/usvo_index.db`
  (`usvo_index` с нормализованными колонками children_count/disability_group/age/locality/… + EAV
  `usvo_attr` для `attr:<key>`) → блок «Точные данные по всей базе» в промпте. Планировщик —
  Dify `usvo_filter_assistant.yml` (`dify.usvo_ai.filter_app_key`), иначе офлайн `plan_offline_v2`.
  Endpoint `POST /api/web/usvo/query`. NB: кириллицу SQLite `lower()` не сворачивает — есть UDF `pyfold`.
  Старый `usvo_query_assistant.yml`/`QuerySpec` — легаси-фолбэк. При `intent=semantic` идёт обычный RAG.
- Знания чата (RAG-ветка) — `web/usvo_knowledge.py`: одна актуальная карточка = один Dify-документ
  `Карточка УСВО #<public-id>: <ФИО>`, мета-агрегаты = `Мета-информация УСВО`.
  Отдельный клиент настраивается через `dify.usvo_ai.app_key` и
  `dify.usvo_ai.dataset.{api_key,dataset_id}`. CRUD/импорт карточек синхронизирует знания
  best-effort; ручная полная пересборка — admin-only
  `POST /api/web/ai-knowledge/usvo/rebuild`. Перед запросом чат сравнивает хэш актуального
  набора с `ai_sync_state`, поэтому изменение основной Excel-таблицы по `mtime` также приводит
  к автоматической пересборке.
- Ответы LLM проходят `linkify_usvo_names`: ссылка ставится только для точного уникального ФИО,
  уже существующие Markdown/HTML-ссылки не оборачиваются повторно. Канонический URL:
  `/usvo/cards/<public-id>`; `main.py` отдаёт по нему SPA, а `app.js` открывает карточку.
- Инструкция ручной настройки Dify и системный промпт — `microservice/USVO_CHAT_PROMPT.md`.

`data/schedule.yaml` — намеренно человекочитаемый, **часто меняемый** файл (здания + слоты),
перечитывается на каждый запрос без перезапуска.

## Особенности MAX Bot API (выучено на практике — легко споткнуться)

- База **`https://platform-api.max.ru`**, авторизация заголовком **`Authorization: <token>`**
  (НЕ query `?access_token=`; старый `botapi.max.ru` устарел).
- В `POST /messages` получатель (`chat_id`/`user_id`) идёт **query-параметром**, иначе ответ
  400 `"Unknown recipient"`.
- **Бот получает `message_created` из группового чата, только если он там администратор.** Callback-и
  (нажатия кнопок) приходят всегда. Поэтому ответ оператора в чате операторов не дойдёт до бота, пока
  бот не админ — это не баг кода.
- Два режима доставки, **взаимоисключающие**: webhook (нужен публичный HTTPS с доверенным серт.) или
  long polling. Нельзя держать активную подписку и polling одновременно.
- Ответ оператора ловится двумя путями: кнопка «Ответить» (ставит `operator_state`) ИЛИ **reply** на
  сообщение-эскалацию (матч по сохранённому `operator_msg_mid`).

## Состояние и счётчики (`store.py`)

SQLite с авто-миграциями (`_ensure_column`) — новые столбцы добавляются к существующей `state.db`,
ничего удалять не нужно. Предложение записи на приём считается по `questions_since_offer` (сбрасывается
в 0 при показе оффера) — это «считать от прошлого предложения», а не по абсолютному счётчику.
