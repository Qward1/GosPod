# INFO.md — архитектура сервиса «Господдержка СВО»

Документ описывает **всю** архитектуру проекта: что где лежит, как одно работает
через другое, какие инструменты используются, дорожные карты запросов (от приёма
из MAX до обратной отправки пользователю) и FAQ из 10 вопросов.

---

## 1. Что это такое

Проект — контакт-центр господдержки участников СВО и их семей. Состоит из **двух
связанных артефактов**:

1. **`assistant.yml`** — экспорт Dify-приложения (тип `advanced-chat`). Это
   оркестратор LLM-сценариев: классификация запроса, ответ по базе знаний,
   генерация справок, тональность/суммаризация. Импортируется в Dify, локально не
   запускается. Рядом — два узких Dify-ассистента: `vision_assistant.yml`
   (распознавание документов по фото) и `document_assistant.yml` (заполнение
   заявления).
2. **`microservice/`** — FastAPI-сервис на Python. Делает то, что Dify не умеет:
   - извлекает данные из Excel для «задания 1» (узкий HTTP-эндпоинт);
   - содержит **всю stateful-логику MAX-бота** (диалог, счётчики, callback-кнопки,
     эскалация операторам, запись на приём, оформление заявлений по фото);
   - раздаёт **веб-кабинет** оператора/администрации (обращения, карточки УСВО,
     заявления, аналитика с тепловой картой);
   - авторизацию кабинета в стиле ЕСИА (на базе Ory Kratos / demo-режим).

Разделение ответственности простыми словами: **Dify «думает» (LLM), микросервис
«помнит и действует» (состояние, кнопки, файлы, операторы, кабинет).**

Исходные файлы пользователя в корне (`Информация для базы знаний.txt`,
`Spravka_Shablon (1).docx`, `USVO_*.xlsx`, шаблоны `.docx`, `Ассистент Цифровой
Офис.yml`) — это входные данные/референс, **не код**.

---

## 2. Технологический стек (инструменты)

| Слой | Инструмент | Зачем |
|------|-----------|-------|
| Веб-фреймворк | **FastAPI 0.115** + **uvicorn 0.34** | HTTP API, вебхук MAX, статика кабинета |
| Конфиг/валидация | **Pydantic 2.10** + **PyYAML 6** | конфиг грузится из YAML (на сервере `.env` запрещён) |
| HTTP-клиент | **httpx 0.28** (async) | вызовы MAX API, Dify, OpenRouter, Kratos |
| Excel | **openpyxl 3.1** | парсинг `.xlsx` (таблица УСВО и «задание 1») |
| Хранилище | **sqlite3** (stdlib, WAL) | состояние бота: вопросы, эскалации, приёмы, заявления |
| Формы | **multipart** | приём файлов в `/excel/extract` |
| DOCX | **свой генератор** `app/common/docx.py` | сборка `.docx` без сторонних либ (zip+XML) |
| Оркестрация LLM | **Dify** (`advanced-chat` workflow) | классификация, ответы по БЗ, справки, vision |
| LLM | **qwen3-32b-fp8-v2** (через `langgenius/openai_api_compatible`) | основная модель в Dify (лимит входа 32k) |
| DOCX-плагин Dify | **scibox/document-generator** | генерация справки (задание 1) |
| Мессенджер | **MAX Bot API** (`platform-api.max.ru`) | канал общения с гражданином |
| Vision (опц.) | **OpenRouter** или Dify-vision | распознавание документов по фото |
| Авторизация | **Ory Kratos** (или demo) | вход в веб-кабинет (ЕСИА-стиль) |
| Фронтенд | **ванильный JavaScript** (SPA, без сборки/бандлера и внешних зависимостей) | веб-кабинет: рендер разделов, темы, переключатель тематик |
| Карта | **Leaflet + leaflet.heat** (CDN), тайлы OSM | тепловая карта в аналитике |

Зависимости — `microservice/requirements.txt` (намеренно минимальны). Формального
тест-фреймворка/линтера нет; проверка — `python -m compileall` + одноразовые
скрипты на `fastapi.testclient` и фейковых клиентах MAX/Dify.

---

## 3. Где что лежит (карта файлов)

```
Господдержка СВО/
├── assistant.yml               # Dify-граф (оркестратор всех LLM-сценариев)
├── vision_assistant.yml        # Dify: распознавание документов по фото (шаг ①)
├── document_assistant.yml      # Dify: заполнение заявления + generate_docx (шаг ②)
├── PROMPTS.md                  # промты для Dify-ассистентов (vision/fill)
├── INFO.md                     # этот файл
│
├── Информация для базы знаний.txt   # исходный текст БЗ (референс/офлайн-фолбэк)
├── *.docx, USVO_*.xlsx              # шаблоны и данные (входные, не код)
│
└── microservice/
    ├── config.yaml             # РАБОЧИЙ конфиг (секреты, ключи) — не в git
    ├── config.example.yaml     # шаблон конфига
    ├── requirements.txt        # зависимости Python
    ├── maxctl.py               # CLI для настройки/диагностики MAX-бота
    ├── README.md               # инструкция по запуску
    │
    ├── data/
    │   ├── state.db            # SQLite-состояние бота (создаётся сам)
    │   └── schedule.yaml       # здания + слоты приёма (часто меняемый, человекочитаемый)
    │
    ├── deploy/kratos/          # docker-compose и конфиги Ory Kratos (реальная авторизация)
    │
    └── app/
        ├── main.py             # точка входа FastAPI: lifespan, сборка app.state, роутеры, статика
        ├── config.py           # загрузка/валидация YAML-конфига (Pydantic), get_config() кэширует
        │
        ├── common/
        │   ├── health.py       # GET /health, GET /schedule
        │   └── docx.py         # сборка .docx из абзацев (zip+XML, без либ)
        │
        ├── excel/              # ЗАДАНИЕ 1: извлечение данных из Excel
        │   ├── router.py       # POST /excel/extract (multipart/file_url/raw body, X-Api-Key)
        │   └── extractor.py    # «легенда + индексы» → компактный текст под 32k
        │
        ├── max/                # MAX-БОТ (вся stateful-логика)
        │   ├── router.py       # POST /max/webhook (приём апдейтов)
        │   ├── polling.py      # фоновый long polling (альтернатива вебхуку)
        │   ├── bot_logic.py    # ★ ВСЯ логика бота (диалог, кнопки, эскалация, заявления)
        │   ├── client.py       # тонкая обёртка над MAX Bot API (отправка, файлы, callback)
        │   ├── dify_client.py  # вызов MAX-ветки Dify + запись ответа оператора в БЗ
        │   ├── store.py        # SQLite-состояние (users/questions/escalations/appointments/applications)
        │   └── schedule.py     # чтение data/schedule.yaml (на каждый запрос)
        │
        ├── docs/               # сценарий «мера поддержки по фото»
        │   ├── doc_ai.py       # DocAI: vision-распознавание + заполнение (Dify/OpenRouter/фолбэк)
        │   └── forms.py        # схема заявления, normalize_application, build_application_docx
        │
        ├── auth/               # авторизация веб-кабинета (ЕСИА-стиль)
        │   ├── service.py      # demo/kratos проверка учётки + HMAC-cookie-сессия
        │   ├── router.py       # /api/web/auth/login|logout|whoami|config
        │   └── deps.py         # require_user (гейт для всего /api/web/*)
        │
        └── web/                # ВЕБ-КАБИНЕТ «Контакт-центр»
            ├── router.py       # /api/web/* (обращения, УСВО, заявления, аналитика)
            ├── service.py      # ★ сервисный слой кабинета (связывает БД, УСВО, Dify)
            ├── usvo.py         # чтение таблицы УСВО (карточки, «поручение Главы округа»)
            ├── analytics.py    # метрики + тепловая карта Ленинского ГО
            ├── history.py      # динамика обращений (графики)
            ├── insight.py      # тональность/суммаризация обращения
            ├── topics.py       # классификация темы обращения
            ├── ai.py           # ИИ-черновики ответов оператора (Dify/OpenAI/локально)
            └── static/         # фронтенд (SPA на ванильном JS)
                ├── index.html, login.html
                ├── app.js, styles.css
                ├── medals.js + medals/   # награды на карточках УСВО
                └── favicon.svg
```

★ — самые важные файлы.

---

## 4. Точка входа и сборка приложения (`app/main.py`)

При старте `uvicorn app.main:app`:

1. `lifespan()` вызывает `get_config()` → читает `config.yaml` (или `CONFIG_PATH`).
2. Создаёт **синглтоны** и кладёт их в `app.state`:
   - `Store(cfg.storage.sqlite_path)` — SQLite-состояние (создаёт таблицы/миграции);
   - `MaxClient(api_base_url, bot_token)` — клиент MAX API;
   - `DifyClient(base_url, max_app_key, dataset_*)` — клиент Dify (ответы + запись в БЗ);
   - `DocAI(cfg)` — vision + заполнение заявлений;
   - `AuthService(cfg)` — авторизация кабинета;
   - `MaxBot(cfg, store, max_client, dify, doc_ai)` — **мозг бота**;
   - при `web.enabled` — `UsvoStore` (таблица УСВО) и `WebService` (кабинет).
3. Если `max.polling=true` — поднимает фоновую задачу `poll_loop` (long polling).
4. Подключает роутеры: `common` (`/health`,`/schedule`), `excel` (`/excel/*`),
   `max` (`/max/*`), `auth` (`/api/web/auth/*`), `web` (`/api/web/*`).
5. **После роутеров** монтирует статику кабинета на `/` (`StaticFiles(html=True)`),
   чтобы API-пути не перехватывались.

Все запросы достают зависимости из `request.app.state` — единое состояние процесса.

---

## 5. Конфигурация (`config.yaml`)

Грузится **из YAML, не из `.env`** (на сервере `.env` запрещён). Путь: `CONFIG_PATH`
или `./config.yaml`. `get_config()` кэшируется (`lru_cache`). Основные секции
(полный список — `app/config.py`, шаблон — `config.example.yaml`):

- `server` — `host/port`, `api_key` (X-Api-Key для `/excel/extract`).
- `excel` — `max_chars` (бюджет компактного текста), `drop_empty_cells`.
- `dify` — `base_url`, `max_app_key` (MAX-ветка), `doc_vision_app_key`,
  `doc_fill_app_key`, `dataset.{api_key,dataset_id}` (запись в БЗ),
  `vision.{provider,openrouter_*}`.
- `max` — `api_base_url`, `bot_token`, `operator_chat_id`,
  `questions_before_offer` (после скольких вопросов предлагать приём),
  `confidence_threshold` (порог уверенности ответа из БЗ),
  `topic_threshold` (порог тематического фильтра), `polling`.
- `storage.sqlite_path` — путь к `state.db`.
- `web` — кабинет: `usvo_xlsx`, `kb_text`, `contact_stale_days`, `operators`,
  `ai.*` (ИИ-черновики), `seed_appeals`.
- `auth` — `mode` (demo/kratos), `kratos_public_url`, `session_secret`,
  `session_ttl_hours`, `demo_users`.
- `schedule_file` — путь к `data/schedule.yaml`.

Плейсхолдеры (`change-me`, `replace_with`, `xxxxxxxx`) трактуются как «не настроено»
— компоненты тогда корректно деградируют (фолбэки), а не падают.

---

## 6. Хранилище данных (`app/max/store.py`, SQLite)

Файл `data/state.db`, режим WAL, соединение открывается на каждую операцию.
**Авто-миграции** через `_ensure_column` — новые столбцы добавляются к существующей
БД, ничего удалять не нужно. Таблицы:

| Таблица | Что хранит | Ключевые поля |
|---------|-----------|---------------|
| `users` | пользователи MAX + счётчики | `question_count`, `questions_since_offer` |
| `questions` | история вопросов (для «последних трёх») | `text`, `created_at` |
| `escalations` | вопросы, переданные операторам | `status`, `operator_id`, `operator_msg_mid`, `answer` |
| `operator_state` | кто из операторов сейчас отвечает | `operator_id → escalation_id` |
| `appointments` | выбранные слоты записи на приём | `building_*`, `time` |
| `applications` | заявления (мера по фото) | `status`, `measure_*`, `data` (JSON), `decided_by` |

Счётчик предложения записи на приём (`questions_since_offer`) считается **от
прошлого предложения** и сбрасывается при показе оффера.

Прочие источники данных:
- `data/schedule.yaml` — здания и слоты приёма; **перечитывается на каждый запрос**,
  правки применяются без перезапуска (`app/max/schedule.py`).
- `USVO_*.xlsx` — таблица УСВО, источник карточек и аналитики (`app/web/usvo.py`).
- `Информация для базы знаний.txt` — текст БЗ для офлайн-предложений кабинета.

---

## 7. MAX-бот (`app/max/`) — детально

**Приём апдейтов** — два взаимоисключающих режима:
- **webhook**: MAX шлёт `POST /max/webhook` → `router.py` → `bot.handle_update()`.
  Требует публичный HTTPS с доверенным сертификатом.
- **long polling**: `polling.py::poll_loop` сам ходит в `GET /updates` (для localhost
  без HTTPS). Нельзя держать активную подписку и polling одновременно.

**`bot_logic.py::MaxBot`** — диспетчер `handle_update()`:
- `message_callback` → `_handle_callback()` (нажатия inline-кнопок);
- `message_created`/`message` → `_handle_message()` (текст/вложения).

`_handle_message()` маршрутизирует:
1. **Чат операторов** (`chat_id == operator_chat_id`) → `_handle_operator_message()`
   (ответ оператора: кнопка «Ответить» **или** reply на сообщение-эскалацию).
2. **Гражданин прислал вложение**, и есть заявление в статусе `awaiting_confirm` →
   `_submit_application(user_files=...)` (присланный файл становится заявлением).
3. **Гражданин прислал фото** (без активного заявления) → `_handle_documents()`
   (сценарий «мера по фото»).
4. **Текст** → `_handle_user_question()`.

**`client.py::MaxClient`** — обёртка над MAX Bot API. Важные нюансы (выучено на
практике):
- база `https://platform-api.max.ru`, авторизация заголовком `Authorization: <token>`;
- получатель (`chat_id`/`user_id`) — **query-параметр** (иначе 400 «Unknown recipient»);
- при 400 по `chat_id` повтор по `user_id` (личный диалог);
- `send_document` загружает файл в два шага (`/uploads` → upload-URL → token) и
  **повторяет отправку** при `attachment.not.ready` (файл обрабатывается асинхронно);
- `answer_callback` без текста **не делает HTTP-запрос** (MAX требует
  `message`/`notification`; «крутилку» снимает удаление сообщения с кнопкой);
- бот получает `message_created` из группового чата, **только если он там админ**;
  callback-и приходят всегда.

**`dify_client.py::DifyClient`**:
- `ask(query)` → `POST {base}/chat-messages` с `inputs={"channel":"max"}`; ответ —
  JSON-строка, парсится `_parse_structured()` в dict
  `{answer, found_in_kb, confidence, on_topic, topic_confidence}`. При сбое парсинга
  «падает открыто» (`on_topic=True`, escalate), чтобы не потерять живого человека.
- `add_to_kb(question, answer)` → `POST /datasets/{id}/document/create-by-text`
  (запись ответа оператора в базу знаний через Dataset API).

---

## 8. Dify-граф (`assistant.yml`) — детально

Граф — дерево с **роутером на старте** (`Start` имеет текстовый вход `channel`):

- **`channel == "max"`** → ветка MAX-бота:
  `knowledge-retrieval` (`max_kb`) → LLM (`max_llm`, structured_output) →
  code (`max_pack`) → answer. Ответ — **JSON-строка**
  `{answer, found_in_kb, confidence, on_topic, topic_confidence}`, которую парсит
  микросервис. Ветка физически не связана рёбрами с заданием 1 (требуемая изоляция).
  - **Тематический фильтр + проверка адекватности живут в системном промпте
    `max_llm`**: модель сама решает `on_topic`/`topic_confidence` (с few-shot
    примерами и «решение-первым» порядком полей в схеме).
  - **`max_pack`** собирает чистый JSON. Содержит явный парсер строковых булевых
    (`"false"`→`False`), т.к. structured_output иногда отдаёт булево строкой
    (`bool("false")` в Python == `True` — иначе фильтр инвертировался бы).
- **иначе** → задание 1: `http-request` → микросервис `/excel/extract` →
  code `parse_excel` → `question-classifier` (2 класса) → «Вопрос по таблице»
  (LLM→answer) **или** «Создание справки» (LLM→code `build_docx`→tool
  `generate_docx`→answer с файлом).

Параметры: модель `qwen3-32b-fp8-v2`, лимит входа **32k токенов**; DOCX —
плагин `scibox/document-generator`; `dataset_ids` и URL микросервиса —
плейсхолдеры, выбираются после импорта. Шаблон справки использует фиксированный
набор плейсхолдеров (`approver_position`, `approver_name`, `reference_title`,
`task_title`, `reference_date`, `protocol_number`, `tasks_completed`).

Соседние ассистенты: `vision_assistant.yml` (шаг ① — распознавание фото) и
`document_assistant.yml` (шаг ② — заполнение заявления + `generate_docx`).

---

## 9. Веб-кабинет (`app/web/`) и авторизация (`app/auth/`)

- Все эндпоинты под `/api/web/*` защищены сессией (`Depends(require_user)`).
  Логин-эндпоинты вынесены в `/api/web/auth/*` и под гейт не попадают.
- **Авторизация** (`auth/service.py`): `mode=demo` (локальный список `demo_users`,
  работает без Kratos) или `mode=kratos` (Identity API Kratos). Сессия — собственная
  cookie, подписанная HMAC-SHA256 по `session_secret`. Гейт фронта — клиентский
  (whoami → `login.html`). В видимом UI название «Ory Kratos» намеренно не
  упоминается.
- **Разделы кабинета**: Обращения, Карточки УСВО, **Заявления**, Аналитика.
  Реальные обращения берутся из таблицы `escalations` (их создаёт бот); если их нет
  и включён `seed_appeals` — синтезируются из карточек УСВО.
- **Аналитика** (`analytics.py`): метрики + **настоящая тепловая карта** Ленинского
  ГО (Leaflet + leaflet.heat, тайлы OSM; `hotspots` — реальные `lat`/`lng` посёлков).
- **Медали** на карточках УСВО (`static/medals.js`): для наград с фото — `<img>`,
  иначе — SVG-фолбэк.

---

## 10. Дорожные карты запросов

### Карта A. Текстовый вопрос гражданина из MAX → ответ

```
Гражданин пишет «Какие выплаты после ранения?» в MAX
   │
   ▼ (webhook ИЛИ polling)
[max/router.py POST /max/webhook]  или  [max/polling.py poll_loop]
   │  update (JSON)
   ▼
[max/bot_logic.py: handle_update → _handle_message]
   │  не чат операторов, текст без вложений
   ▼
[bot_logic.py: _handle_user_question]
   │  store.ensure_user(); since_offer = store.add_question()   → пишет в users/questions (state.db)
   ▼
[max/dify_client.py: ask("...", channel=max)]
   │  POST {dify}/chat-messages
   ▼
[Dify assistant.yml: max_kb → max_llm → max_pack → answer]
   │  возвращает JSON-строку
   ▼
[dify_client._parse_structured] → {answer, found_in_kb, confidence, on_topic, topic_confidence}
   │
   ├─ ФИЛЬТР: on_topic=false И topic_confidence ≥ topic_threshold?
   │     └─ да → send_message(MSG_OFF_TOPIC) → STOP (ни БЗ, ни оператора)
   │
   ├─ needs_operator = (не found_in_kb) ИЛИ (confidence < confidence_threshold)?
   │     ├─ да → store.create_escalation()  → escalations (state.db)
   │     │       _notify_operators_escalation() → MAX чат операторов (кнопка «Ответить»)
   │     │       store.set_escalation_mid()  → сохраняет mid для reply
   │     │       send_message(MSG_ESCALATED) → гражданину
   │     └─ нет → send_message(result.answer) → гражданину
   │
   ▼
если since_offer ≥ questions_before_offer:
   _offer_appointment() → кнопки «Записаться/Отказаться»; store.reset_since_offer()
   │
   ▼
[max/client.py: send_message] → POST {max}/messages?chat_id=... (retry по user_id)
   │
   ▼
Гражданин видит ответ из БЗ ИЛИ «передано оператору»
```

Файлы по пути: `max/router.py|polling.py` → `max/bot_logic.py` →
`max/dify_client.py` → (Dify `assistant.yml`) → `max/store.py` (запись) →
`max/client.py` (отправка).

### Карта B. Ответ оператора → пользователю + запись в БЗ

```
Оператор в чате операторов нажимает «Ответить» (или reply на эскалацию) и пишет ответ
   │
   ▼
[bot_logic.py: _handle_message] chat_id == operator_chat_id
   ▼
[bot_logic.py: _handle_operator_message]
   │  store.pop_operator_state()  ИЛИ  store.get_open_escalation_by_mid(reply_mid)
   ▼
[bot_logic.py: _process_operator_answer]
   ├─ client.send_message(ответ) → гражданину (esc.user_chat_id)
   ├─ dify_client.add_to_kb(question, answer) → POST /datasets/{id}/document/create-by-text
   ├─ store.set_escalation_answer() + store.close_escalation()   → escalations
   └─ client.send_message(отчёт) → чат операторов (кому/вопрос/ответ/статус записи в БЗ)
```

### Карта C. Запись на приём

```
Гражданин нажимает «Записаться» (callback a=yes)
   ▼
[bot_logic.py: _handle_callback] → _send_slots()
   │  schedule.iter_slots(schedule.yaml)  → кнопки «Здание · время» (a=slot)
   ▼
Гражданин выбирает слот (callback a=slot, b=building, t=time)
   ▼
[bot_logic.py: _handle_slot_selection]
   ├─ schedule.find_building()  (data/schedule.yaml)
   ├─ store.create_appointment()                 → appointments (state.db)
   ├─ store.last_questions(user, 3)              → последние 3 вопроса
   ├─ client.send_message(заявка) → чат операторов (контакты + слот + история)
   └─ client.send_message(подтверждение) → гражданину
```

### Карта D. Оформление меры поддержки по фото

```
Гражданин присылает фото документов
   ▼
[bot_logic.py: _handle_message → _handle_documents]
   ├─ send_message(MSG_DOC_ANALYZING)
   ├─ doc_ai.analyze_documents(urls)             # docs/doc_ai.py
   │     ├─ vision: Dify (doc_vision_app_key) ИЛИ OpenRouter ИЛИ ЛОКАЛЬНЫЙ фолбэк
   │     └─ forms.normalize_application()        # docs/forms.py
   ├─ store.create_application(status="proposed", data=JSON)   → applications
   └─ send_message(мера + «Оформить/Отказаться»  a=doc_make/doc_no)
        │
        ▼ гражданин нажал «Оформить» (a=doc_make)
[bot_logic.py: _finalize_application]
   ├─ doc_ai.fill_application()                  # Dify doc_fill_app_key ИЛИ фолбэк
   ├─ store.update_application_data() + set_application_status("awaiting_confirm")
   ├─ forms.build_application_docx()             # docs/forms.py → common/docx.py
   └─ client.send_document(.docx, «Подтвердить и подать/Отказаться»)   # retry на not.ready
        │
        ├── ВАРИАНТ 1: гражданин нажал «Подтвердить и подать» (a=doc_ok)
        │      └─ _submit_application()
        │
        ├── ВАРИАНТ 2: гражданин вместо кнопки прислал свой файл
        │      └─ _handle_message видит pending application (awaiting_confirm)
        │         → _submit_application(user_files=[...])  (файл = заявление)
        │
        └── ВАРИАНТ 3: «Отказаться» (a=doc_no)
               └─ сообщение исчезает, _cancel_application(); можно задавать вопросы
        │
        ▼ (варианты 1/2)
[bot_logic.py: _submit_application]
   ├─ store.set_application_status("submitted")  (+ data.user_files при варианте 2)
   ├─ send_message(«Заявление принято…») → гражданину
   └─ send_message(новое заявление + ссылки на файлы) → чат операторов
        │
        ▼
Заявление видно в веб-кабинете → раздел «Заявления» (одобрить/отклонить/скачать .docx)
```

### Карта E. Задание 1 — Excel → справка (Dify-ветка)

```
Пользователь в Dify (channel != "max") прикладывает .xlsx и спрашивает
   ▼
[Dify assistant.yml: http-request]  POST {микросервис}/excel/extract  (X-Api-Key)
   ▼
[excel/router.py: extract]  принимает файл (multipart / file_url / raw body)
   ▼
[excel/extractor.py: extract_xlsx]  → «легенда столбцов + записи с индексами», под 32k
   ▼  {text, rows, columns, truncated, ...}
[Dify: code parse_excel] → [question-classifier: 2 класса]
   ├─ «Вопрос по таблице» → LLM → answer
   └─ «Создание справки» → LLM → code build_docx → tool generate_docx (scibox) → answer с файлом
```

### Карта F. Запрос веб-кабинета

```
Браузер → GET / (статика index.html)
   ▼ app.js: fetch /api/web/auth/whoami
   ├─ не авторизован → редирект на login.html → POST /api/web/auth/login (demo/kratos) → cookie-сессия
   ▼ авторизован
fetch /api/web/appeals | /usvo | /applications | /analytics
   ▼
[web/router.py]  Depends(require_user)  → [web/service.py: WebService]
   ├─ источники: store (state.db), usvo.py (USVO.xlsx), dify (черновики/подсказки)
   └─ JSON → app.js рендерит разделы (включая тепловую карту Leaflet)
```

---

## 11. Запуск и эксплуатация

Из каталога `microservice/`:

```bash
pip install -r requirements.txt
cp config.example.yaml config.yaml      # затем заполнить ключи
uvicorn app.main:app --host 0.0.0.0 --port 8080

# Настройка/диагностика MAX-бота (нужен заполненный config.yaml):
python maxctl.py me                            # проверить токен (GET /me)
python maxctl.py subscriptions                 # текущие подписки на вебхук
python maxctl.py subscribe <https-url>/max/webhook
python maxctl.py unsubscribe <https-url>/max/webhook
python maxctl.py poll                           # long polling (для localhost без HTTPS)

# Проверки (формального фреймворка нет):
python -m compileall -q app maxctl.py           # синтаксис
```

Ключевые URL: `GET /health`, `GET /schedule`, `POST /excel/extract`,
`POST /max/webhook`, `/api/web/auth/*`, `/api/web/*`, статика кабинета на `/`.

---

## 12. FAQ — 10 вопросов по сервису

**1. Почему логика бота в микросервисе, а не в Dify?**
Dify (`advanced-chat`) — stateless-оркестратор LLM: он не умеет вести диалог с
callback-кнопками, счётчиками вопросов, эскалацией операторам и отправкой файлов.
Всё, что требует **состояния и действий**, живёт в `app/max/bot_logic.py`. Dify
отвечает только за «подумать»: классификация запроса и ответ по базе знаний.

**2. Как сервис понимает, что вопрос не по теме (например «как дойти до кафе»)?**
Решает **LLM**, а не ключевые слова. В системном промпте узла `max_llm`
(`assistant.yml`) есть шаг классификации с few-shot-примерами; модель возвращает
`on_topic` и `topic_confidence`. В `bot_logic._handle_user_question` запрос
отклоняется (базовым `MSG_OFF_TOPIC`, без эскалации), если `on_topic=false` И
`topic_confidence ≥ max.topic_threshold` (по умолчанию 0.5). При сомнении/сбое
парсинга считаем запрос релевантным — чтобы не отсечь живого человека.

**3. Когда вопрос уходит оператору?**
Если ответа в БЗ нет или модель не уверена: `needs_operator = (not found_in_kb) or
(confidence < max.confidence_threshold)`. Тогда создаётся эскалация
(`store.create_escalation`), в чат операторов летит сообщение с кнопкой «Ответить»,
а гражданину — «передано оператору».

**4. Как ответ оператора попадает обратно пользователю и в базу знаний?**
Оператор отвечает кнопкой «Ответить» (ставит `operator_state`) **или** reply-ом на
сообщение-эскалацию (матч по сохранённому `operator_msg_mid`).
`_process_operator_answer` шлёт ответ гражданину, пишет пару вопрос/ответ в БЗ через
`DifyClient.add_to_kb` (Dataset API), закрывает эскалацию и отправляет отчёт в чат
операторов. Важно: **бот видит сообщения группового чата, только если он там
администратор** — иначе ответ ловится лишь по callback-кнопке.

**5. Webhook или long polling — что выбрать?**
Это **взаимоисключающие** режимы. Webhook (`POST /max/webhook`) нужен для прод-сервера
с публичным HTTPS и доверенным сертификатом. Long polling (`max.polling=true` или
`maxctl.py poll`) — для localhost без HTTPS. Нельзя держать активную подписку на
вебхук и polling одновременно.

**6. Как работает оформление меры поддержки «по фото»?**
Гражданин присылает фото документов → `DocAI.analyze_documents` распознаёт их
(vision-модель Dify/OpenRouter или офлайн-фолбэк) и предлагает меру → кнопка
«Оформить» запускает `fill_application` и сборку `.docx` (`forms.build_application_docx`)
→ гражданин подтверждает кнопкой **или** присылает свой файл → статус `submitted`,
операторы получают уведомление, заявление видно в разделе «Заявления». Каждый шаг
имеет детерминированный офлайн-фолбэк, так что сценарий работает даже без vision.

**7. Зачем своя генерация .docx, если у Dify есть плагин?**
Плагин `scibox/document-generator` используется в Dify для **справки** (задание 1).
А `.docx`-**заявление** в MAX-боте собирается микросервисом (`app/common/docx.py`,
zip+XML) — чтобы не зависеть от Dify в stateful-сценарии и не тянуть тяжёлые
библиотеки (python-docx и т. п.).

**8. Почему конфиг в YAML, а не в `.env`?**
На целевом сервере `.env` запрещён. Конфиг грузится из `config.yaml` (или
`CONFIG_PATH`) через Pydantic-модели в `app/config.py`, `get_config()` кэширует.
Незаполненные плейсхолдеры (`change-me` и т. п.) трактуются как «не настроено», и
компоненты деградируют до фолбэков, а не падают.

**9. Откуда веб-кабинет берёт данные и как защищён?**
Обращения — из таблицы `escalations` (их создаёт бот); если их ещё нет и включён
`web.seed_appeals` — синтезируются из карточек УСВО. Карточки и аналитика — из
`USVO_*.xlsx` (`app/web/usvo.py`), заявления — из таблицы `applications`. Все
`/api/web/*` защищены сессией (`require_user`); вход — demo-режим (локальные
`demo_users`) или Kratos. Сессия — своя HMAC-cookie.

**10. Как влезть в лимит входа LLM 32k при большой Excel-таблице?**
`app/excel/extractor.py` выносит длинные названия столбцов в **легенду один раз**, в
записях использует числовые индексы `[N]`, выбрасывает пустые ячейки и приводит даты
к `ДД.ММ.ГГГГ`. Есть бюджет `excel.max_chars` с флагом `truncated`. Эндпоинт
`/excel/extract` принимает файл тремя способами (multipart `file` / `file_url` / raw
body), чтобы не зависеть от версии Dify-узла.
