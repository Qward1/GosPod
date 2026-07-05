# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

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

Формального тест-фреймворка/линтера нет. Проверка делается так:
```bash
python -m compileall -q app maxctl.py          # синтаксис
# поведение — через fastapi.testclient.TestClient (одноразовые скрипты) и фейковые
# MaxClient/DifyClient для прогона bot_logic без сети.
```

## Архитектура: Dify (`assistant.yml`)

Граф — дерево с **роутером на старте**. В `Start` есть текстовый вход `channel`:
- `channel == "max"` → ветка MAX: `knowledge-retrieval` → LLM (structured_output) → code `max_pack`
  → answer. **Ответ — это JSON-строка** `{answer, found_in_kb, confidence, on_topic, topic_confidence}`,
  которую парсит микросервис. **Тематический фильтр живёт в системном промпте `max_llm`**: модель сама
  классифицирует, относится ли запрос к профилю контакт-центра (господдержка СВО, помощь УСВО и семьям,
  получение информации по этим темам), и заполняет `on_topic`/`topic_confidence`. Все 5 полей —
  `required` в схеме structured_output, и `max_pack` обязан их прокидывать. Ветка MAX **физически
  не связана рёбрами** с заданием 1 — это и есть требуемая изоляция.
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
  в `app.state`; при `max.polling=true` поднимает фоновый `poll_loop`. **Веб-роутер и auth-роутер
  подключаются дважды** — без префикса и с `prefix="/application"`; статика тоже смонтирована и на `/`,
  и на `/application` (для деплоя за reverse-proxy, отдающим кабинет по подпути). Все API веб-кабинета
  живут под `/api/web` (`web/router.py`, `APIRouter(prefix="/api/web", dependencies=[require_user])`).
- `auth/` — авторизация веб-кабинета в стиле ЕСИА (бэкенд на Ory Kratos). `service.py` (demo/kratos +
  HMAC-cookie-сессия), `router.py` (`/api/web/auth/login|logout|whoami|config`), `deps.py`
  (`require_user`, им router-level защищён весь `/api/web/*`). Демо-режим работает без Kratos; реальный
  Kratos — `deploy/kratos/`. Гейт фронта — клиентский (whoami → `login.html`). **В самом интерфейсе
  (`login.html`) название «Ory Kratos» намеренно не упоминается** — только нейтральные формулировки;
  при правке UI не возвращать его в видимый текст.
- **Роли (админ / сотрудник)** — две роли различаются по `auth.service.is_admin_role(role)` («админ»/
  «admin» в названии → админ, иначе сотрудник). Админские учётки лежат в `auth.demo_users` (config),
  **учётки сотрудников создаёт админ** и хранятся в **отдельной БД** `auth/employees.py::EmployeeStore`
  (`auth.employees_db`, по умолчанию `data/employees.db`; пароли — PBKDF2-HMAC-SHA256 с солью).
  `AuthService.authenticate` сначала проверяет БД сотрудников, затем demo/kratos. `whoami` отдаёт
  `is_admin`. Раздел **«Настройки»** (CRUD сотрудников) — `auth/employees_router.py`
  (`/api/web/settings/employees`, гейт `deps.require_admin` → 403 не-админу), подключается в `main.py`
  дважды (с/без `/application`). `app.state.employees` — единый `EmployeeStore`, его же получает
  `WebService` (конструктор), чтобы `_operators()` = имена активных сотрудников из БД (фолбэк —
  `web.operators` из конфига). **Выбор ответственного по обращению**: сотрудник «привязан» к своей
  учётке (селектор заблокирован на его имени и в сайдбаре, и в карточке обращения — `app.js` ветвит по
  `state.isAdmin`), админ выбирает любого сотрудника. Раздел «Настройки» в навигации и его view
  (`renderSettings`) видны/доступны только админу (`nav__item--admin`, `VIEWS.settings.adminOnly`).
- `docs/` — сценарий «мера поддержки по фото»: `forms.py` (схема заявления ЖКУ + офлайн-сборка .docx
  через `common/docx.py`, без сторонних либ), `doc_ai.py` (`DocAI`: **единый Dify-ассистент документов
  `dify.documents_app_key`** (`documents_assistant.yml`, роутер по входу `task` на ветки
  `read`/`extract`/`translate`/`recognize`) — предпочтительный путь для ВСЕХ документных сценариев,
  заменяет `vision_assistant.yml` и прямые вызовы OpenRouter; когда ключ пуст — старая логика
  (`doc_vision_app_key` / `vision.provider=dify|openrouter`) как фолбэк. **`generate_application_docx`
  формирует .docx в Dify-
  ассистенте `doc_fill_app_key`**: загружает DOCX-шаблон из `dify.doc_template_path` в Dify
  (Files API → переменная `template` узла Start), шлёт поля JSON, скачивает готовый файл из
  `message_files`; при несконфигурированном/недоступном Dify — офлайн-сборка `build_application_docx`).
  **Новый шаблон класть в `data/Zayavlenie_Shablon.docx`** (путь — `dify.doc_template_path`).
  Промты — `PROMPTS.md`; Dify-ассистенты — **`documents_assistant.yml`** (единый, 4 ветки по `task`:
  чтение/извлечение полей/перевод подписей/распознавание ЖКУ — `dify.documents_app_key`; заменяет
  `vision_assistant.yml`, который оставлен как легаси для `doc_vision_app_key`),
  `document_assistant.yml` (заполнение + generate_docx, шаг ②) и
  `measure_assistant.yml` (подбор меры поддержки по тексту, `dify.measure_app_key`,
  structured_output `{measure_id, measure_title, found, confidence}`).
  Клиент MAX (`client.py`) при 400 на отправку по `chat_id` повторяет по `user_id` (личный диалог).
- MAX-бот (`bot_logic.py`) кроме вопросов ведёт **flow по фото**: когда гражданин просто присылает
  фотографии документов, `_handle_documents` **читает их текст визуальной моделью**
  (`DocAI.read_documents_text` — ветка `read` единого ассистента `documents_app_key`, иначе
  легаси `dify`/`openrouter`, офлайн-фолбэк `_DEMO_DOCS_SUMMARY`)
  и **подбирает меру через ассистент `select_measure` + базу знаний мер** (`measure_assistant.yml`,
  `dify.measure_app_key`; офлайн — `match_measure_offline`). Подобранная мера (из заведённых админом,
  без хардкода) предлагается кнопкой «Оформить» с callback **`sm_pick`** → дальше идёт общий сценарий
  «Меры поддержки» (пошаговый сбор документов, см. ниже). Порог уверенности подбора — 0.5; если меры
  не заведены — `MSG_SM_NONE`, если не подобралась — `MSG_DOC_NO_MEASURE` + кнопка входа в меню мер.
  **Старый зашитый ЖКУ-флоу** (`_finalize_application`/`_submit_application`, callback-и
  `doc_make`/`doc_ok`/`doc_no`, `analyze_documents` → DEFAULT_MEASURE) больше **не запускается с фото**,
  но методы оставлены: их ещё дёргает ветка «гражданин прислал свой файл вместо кнопки подтверждения»
  для заявлений в статусе `awaiting_confirm` (`get_pending_application`) — присланные вложения
  (`_attachment_urls` — фото И документы) кладутся в `data.user_files`, операторам уходят ссылки.
  Заявления — таблица `applications` (`store.py`), видны в админ-разделе «Заявления».
  `_submit_application`/`_cancel_application` не трогают уже `submitted`-заявление.
- **Сценарий «Меры поддержки»** (управляемые администратором меры, в дополнение к зашитому ЖКУ-флоу):
  меры заводит админ в веб-кабинете (раздел «Заявления» → подраздел «Меры поддержки»), хранятся в
  таблице `support_measures` (`store.py`; документы и плейсхолдеры — JSON в `data`, шаблон — файл в
  `dify.measure_templates_dir`). Бот: inline-кнопка/команда «Оформление мер поддержки» → `_send_measure_menu`
  (активные меры из БД, **без хардкода**) → callback `sm_pick` (`_start_measure_flow`: создаёт заявку
  через `create_measure_application`, статус `started/waiting_document`, состояние сбора — JSON-колонка
  `applications.flow`) → **последовательный сбор фото документов** (`_collect_document`, гейт по
  `get_active_flow` в `_handle_message` ДО старого `_handle_documents`) → распознавание
  (`DocAI.extract_fields` по плейсхолдерам меры → `{filled_fields, missing_fields}`) → проверка
  (`templates.check_required`); если полей не хватает — `waiting_missing_fields`, дозапрос одним
  текстом (`_apply_missing_fields`, построчно по порядку) → генерация (`DocAI.generate_measure_docx`
  = офлайн `templates.fill_template_docx` по шаблону меры) → кнопки `sm_ok`/`sm_redo`/`sm_cancel`
  (`_submit_measure_application` ставит `submitted`, уведомляет операторов). Статусы флоу:
  `started, waiting_document, documents_uploaded, extracting_data, waiting_missing_fields,
  ready_for_confirmation` (промежуточные, в кабинете не показываются) → `submitted/cancelled/failed`.
  LLM-подбор меры по тексту — `DocAI.select_measure` (Dify `measure_app_key` / `measure_assistant.yml`
  со structured_output, фолбэк — `measures.match_measure_offline`). Веб-API CRUD —
  `web/measures_router.py` (`/api/web/settings/support-measures/*`, гейт `require_admin`), бизнес-логика —
  `WebService` (`create/update/delete_support_measure`, `save_measure_template`). **Автосинхронизация
  с базой знаний**: после каждой правки роутер зовёт `sync_measure_to_kb`/`remove_measure_from_kb` —
  каждая активная мера лежит в датасете `dify.dataset` (тот же, что для ответов на вопросы и для
  `measure_assistant.yml`) отдельным документом «Мера поддержки #<id>: …», обновляется на месте без
  дублей (`DifyClient.upsert_document_text`/`delete_documents_by_prefix`); кнопка «Синхронизировать с
  ИИ» (`sync_measures_kb`) делает полную пересинхронизацию. Достаточно заполнить `dify.dataset.*`.
  `_application_to_dict`/`application_docx` различают заявки меры (`support_measure_id`/`data.fields`)
  и заполняют .docx подстановкой в шаблон меры. Helpers — `app/docs/measures.py`, `app/docs/templates.py`.
- **Гид-сценарий «Меры поддержки» (стартовый экран из макета)** — надстройка над движком мер выше,
  воспроизводит 5 экранов брифа руководителя. Приветствие `MSG_WELCOME` (экран 1) идёт с тремя
  тематическими кнопками `_topic_rows` (callback `topic`: `measures`/`gkh`/`roads`); ЖКХ и Дороги —
  заглушка `MSG_TOPIC_STUB` («раздел в разработке»). Тема «Меры поддержки» → `_start_guided_flow`:
  опрос родства и региона (экран 2, кнопки `_relation_rows` callback `rel` + свободный текст,
  парсеры `_parse_relation`/`_parse_region`/`_extract_locality`) → сводка профиля с бейджами
  🟢/🔴 и списком активных мер + кнопка **request_contact** «Поделиться номером» (экран 3,
  `_share_phone_rows`, `MaxClient.inline_keyboard` умеет типы кнопок) → приём телефона
  (`_contact_phone` из вложения-контакта или `_looks_like_phone`/`_format_phone` из текста,
  сохраняется в `users.phone`) → выбор меры (одна — сразу, иначе кнопки `sm_pick` с флагом `g:1`)
  → **тот же сбор/распознавание документов, что и у обычного движка мер** (экран 4, интро
  `_send_documents_intro`) → финал `_finalize_guided_measure` (экран 5): заявка сразу `submitted`,
  номер `_application_number` (#СВО-…, формула `99280+id`), кнопки подписки (callback `sub` →
  `users.subscribed`). Отличие от обычного пути: ветвь `guided` во `flow` заявки → `_generate_and_offer`
  вызывает финал по макету вместо подтверждения .docx кнопкой `sm_ok`. Состояние опроса ДО выбора
  меры — таблица `bot_flows` (`store.set/get/clear_bot_flow`, стадии `collect_profile`/`await_phone`/
  `choose_measure`), гейт в `_handle_message` ПОСЛЕ `get_active_flow`, ДО фото/`_handle_documents`.
  **Вступительная анкета (родство/регистрация/телефон) спрашивается только при первом обращении.**
  Собранные поля персистятся в `users` (столбцы `relation`/`region_ok`/`locality`, телефон — в
  `users.phone`; сеттеры `store.set_user_profile`/`clear_user_profile`). `_start_guided_flow` подтягивает
  профиль `_profile_from_db`: полностью собранный (`_profile_complete`) — подставляется без вопросов
  (`MSG_PROFILE_KNOWN` → сразу к выбору меры), частичный — доспрашивается только в недостающей части
  (`_advance_profile` сохраняет каждое поле по мере сбора). **`/start` = «начать сначала»**: чистит
  `bot_flow` и профиль (`clear_user_profile`), чтобы гражданин заново ввёл данные.
  Две меры сценария (школьное питание, детсад) заводятся идемпотентно при старте —
  `measures.seed_demo_measures` (вызов в `main.py`), это настоящие редактируемые записи БД.
- **Тематический фильтр запросов** (`_handle_user_question`): после `dify.ask` запрос считается
  off-topic, если `on_topic=false` И `topic_confidence ≥ max.topic_threshold` (по умолчанию 0.5) —
  тогда бот отвечает базовым `MSG_OFF_TOPIC` и **прекращает обработку: ни ответа из БЗ, ни эскалации
  операторам**. При низкой уверенности фильтра / сбое парсинга (`dify_client._parse_structured`
  по умолчанию `on_topic=True`) запрос идёт обычным путём — чтобы не отсечь живого человека.
- Веб-кабинет — 4 раздела: Обращения, Карточки УСВО, **Заявления** (над аналитикой), Аналитика.
  У части карточек УСВО — строка «Поручение Главы округа» (`usvo.py::_head_directive`). Аналитика
  наполнена демо-данными + **тепловая карта** Ленинского ГО с заметкой ИИ (`analytics.py`).
  В шапке Аналитики — кнопка **«В базу знаний»** (`app.js::openKbUpload`): грузит текст или файл в
  **тот же** Dify-датасет, куда падают ответы операторов — `POST /api/web/kb/upload-text`
  (`dify_client.add_document_text`) и `POST /api/web/kb/upload-file` (`add_document_file`,
  create-by-file). Доступность определяет `meta.kb_ready` (`DifyClient.kb_ready` — настроены
  `dify.dataset.dataset_id` и `dify.dataset.api_key`).
  Раздел один — **Контакт-центр** (прежний переключатель тематик «Земельный контроль»/«Работа с
  должниками» удалён; бренд в сайдбаре теперь статичный `.brand--static`, никакого `data-dept`).
- **Загрузка/выгрузка/фильтры карточек УСВО** (раздел «Карточки УСВО»):
  - **Фильтры** — текстовый поиск + выпадающие фильтры (статус, ВБД, нужна работа, давно без связи,
    в организациях, с наградами, поручение Главы, источник). Бэкенд: `service.list_usvo(query, filters)`
    → `_filtered_records` (флаги из `usvo.compute_flags`); трёхпозиционный фильтр — `service._tri`.
    Статусы для дропдауна отдаёт `meta.usvo_statuses`.
  - **Загрузка из Excel** — `POST /api/web/usvo/import` (multipart `file`, `?replace=`):
    `import_usvo.parse_usvo_xlsx` разбирает любую таблицу, особый столбец «История взаимодействия»
    нормализуется (см. ниже) и всё пишется в **таблицу `usvo_cards`** (`store.py`). Шаблон-пример —
    `GET /api/web/usvo/template` (`import_usvo.build_template_xlsx`, 2 примера строк). Удаление
    загруженной — `DELETE /api/web/usvo/{id}`, очистка — `POST /api/web/usvo/clear`. Ответ импорта
    содержит `saved` и `skipped` (сколько пропущено как дубли).
  - **Редактирование карточки** — инлайн, прямо во вкладках со значениями: каждое поле в блоке
    «Данные участника» — это пара `contenteditable` (название + значение), правится на месте
    (`app.js::bindUsvoInlineEdit`; кнопки «Поле»/«Отменить»/«Сохранить» в шапке блока, текст истории —
    по кнопке «Изменить текст»). При сохранении весь набор полей собирается из DOM
    (`usvoCanonFields` строит каноничный список — дедуп + гарантированные поля шапки, включая награды,
    поэтому скрытые поля не теряются) и уходит `PUT /api/web/usvo/{id}` (`service.update_usvo`).
    Карточка хранится как список полей (`label→value`); ключевые значения (ФИО/статус/телефон/…)
    пересчитываются `find_field_value`. `history_raw` шлётся только если оператор реально менял текст
    истории (иначе бэкенд сохраняет уже нормализованные события без перегенерации).
    Правка карточки **из БД** обновляет строку «на месте» (`store.update_usvo_card`); правка
    **табличной** карточки создаёт загруженный оверрайд (`add_usvo_card`, batch=`edit`), который
    перекрывает исходную по идентичности (см. ниже). У `usvo_cards` есть `updated_at` — без него кэш
    `UsvoCardStore` (сигнатура = число строк + max `updated_at`) не перечитает правку «на месте».
  - **Защита от дубликатов (идентичность карточки)** — `usvo.card_identity(name, birth, phone)` /
    `record_identity(rec)`: устойчивый ключ из ФИО + дата рождения (фолбэк ФИО + телефон, затем
    только ФИО/телефон). Правка второстепенных полей идентичность не меняет. `_all_records()` прячет
    табличную карточку, если её идентичность совпала с уже загруженной (показывается версия из БД с
    правками). `import_usvo` пропускает строки Excel, чьи идентичности уже есть среди карточек
    (`existing` из `_all_records()`), — повторная загрузка «старые + новые» не плодит дубли и
    сохраняет именно отредактированную версию.
  - **Источник карточек** объединён: `service._all_records()` = загруженные из БД
    (`usvo_db.UsvoCardStore`, id со смещением `USVO_DB_ID_BASE=100000`, минус дубли по идентичности)
    **+** табличные из Excel (`UsvoStore`). Загруженная карточка строится `usvo.record_from_fields`
    (классификация полей по смыслу заголовка, не по позиции — каноничную таблицу это не затрагивает).
  - **Выгрузка в Excel** — `GET /api/web/export/{usvo|appeals|applications|analytics}`
    (`export.py`, openpyxl). Выгрузка УСВО уважает текущие фильтры; обращения — с ответами.
- **Нормализация «Истории взаимодействия»** — свободный текст столбца превращается в события
  `{date,kind,status,style,title,detail,org}` (`history_ai.normalize_history`): при настроенном
  `dify.history_app_key` — Dify-ассистент `history_assistant.yml` (подключён к базе знаний
  `data/history_styles.md`), иначе офлайн-нормализатор. Справочник стилей (категории→иконка,
  статусы→цвет, акценты) — `web/history_styles.py` (зеркало `data/history_styles.md`). Фронтенд:
  `renderTimeline` рисует иконку по `kind`, бейдж по `status`, акцент точки по `style`
  (`.tl-style--{ok|accent|planned|warn|danger|info}`). Для загруженных карточек история берётся из БД
  (`service._merge_uploaded_history` = нормализованные события + реальные обращения), для табличных —
  синтетика `history.build_history`.
- **Профиль гражданина MAX + история обращений по его id** (задание 3): бот пишет пользователя в
  таблицу `users` (`ensure_user`), эскалации хранят `user_id`. В панели обращения — блок «История
  обращений гражданина» (`GET /api/web/appeals/{id}/history` → `service.appeal_history`,
  `store.list_escalations_by_user`). Черновик ответа берёт **ФИО из реального профиля MAX**
  (`ai.draft_reply(citizen_name=…)`), а не из случайно сопоставленной карточки УСВО.
- **Тепловая карта** — настоящая интерактивная карта Leaflet (CDN `unpkg`, плагин `leaflet.heat`),
  тайлы OpenStreetMap. `analytics.py` отдаёт `heatmap.center` + `hotspots` с **реальными гео-координатами**
  (`lat`/`lng`) посёлков Ленинского ГО; `app.js::initHeatmapMap` строит карту после вставки разметки
  в DOM (heat-слой + кликабельные маркеры-очаги). Требует сети для тайлов; без неё видны маркеры/heat
  на сером фоне. **Координаты hotspots — это lat/lng, не проценты** (была стилизованная картинка).
- **Медали** на карточках УСВО (`static/medals.js`): для наград с реальным фото (`static/medals/*` —
  «Орден Мужества», «За отвагу», «За боевые отличия», «Благодарность командования») отдаётся `<img>`,
  для остальных — стилизованный SVG-фолбэк. `Medals.resolve(text) → [{name, html}]` (раньше было `svg`).
  Сопоставление — по подстрокам в `MEDALS[].match`; новые фото класть в `static/medals/` ASCII-именем
  и добавлять `img:` в нужную запись. **Путь к фото — абсолютный** (`_photoBase()` повторяет логику
  `app.js::appBaseFromPath`): карточка открывается deep-link'ом `/usvo/cards/<id>` через
  `history.pushState`, поэтому относительный `medals/...` резолвился в `/usvo/cards/medals/...` → 404;
  `_photoBase()` срезает `/usvo/cards/<id>` и учитывает префикс `/application`. При правке `medals.js`
  не забывать поднять cache-buster `?v=` у `<script src="medals.js">` в `index.html`.

- **«Чат с ИИ» по карточкам УСВО — точный поиск через SQLite (Text-to-SQL), а не RAG**
  (`web/ai_chat.py`, `web/usvo_query.py`, `web/usvo_index.py`, `web/usvo_knowledge.py`). Карточек ~1500 —
  все в контекст модели не влезают (лимит 32k), а RAG не отвечает на точные/составные вопросы («сколько
  с >3 детьми», «все с инвалидностью 2 группы и двумя детьми»). Поэтому: вопрос → **JSON-фильтр (DSL)**
  → **валидация безопасности** → **запрос к SQLite** → LLM формулирует ответ. Этапы:
  - **Индекс** (`web/usvo_index.py`) — производная нормализованная проекция всех карточек
    (`web.all_usvo_records()`) в **отдельный** SQLite `data/usvo_index.db` (`dify.usvo_ai.usvo_index_db`,
    полностью derived, источник истины не трогаем). Две таблицы: `usvo_index` — типизированные
    канон-колонки (status, age, locality, **children_count/minor_children**, **disability/disability_group**,
    gender, vbd, unemployed, awards_count, in_org, …; нормализаторы `parse_children`/`parse_disability`/
    `parse_gender` достают числа из свободного текста «сын, 2014 г.р.»/«инвалидность 2 группа») и
    `usvo_attr` — EAV всех полей для произвольных условий `attr:<key>`. Пересобирается лениво при смене
    сигнатуры `(len, web.usvo_last_updated())` — как кэш `UsvoCardStore`.
  - **DSL-фильтр**: `{intent: count|list|aggregate|lookup|semantic, where:{match:all|any, conditions:[…]},
    aggregate_by, order_by, order_dir, limit}`; условие — `{field, cmp, value}` (canon-поле или `attr:<key>`)
    либо вложенная группа (AND/OR, глубина ≤3). `cmp`: `= != > >= < <= contains in between exists`. Поле
    `field`/операторы — **whitelist `FIELDS`** (единственные допустимые имена колонок).
  - **`validate_filter`** (`usvo_index.py`) — отбрасывает неизвестные поля/операторы, коэрцит типы,
    режет глубину/число условий/limit. **SQL строится только параметрически** (имена колонок — из
    whitelist, значения — bind-параметры; `attr` — EXISTS-подзапрос). Инъекция невозможна. NB: кириллицу
    SQLite `lower()` НЕ сворачивает — для регистронезависимого поиска зарегистрирован UDF **`pyfold`**
    (Python `casefold`), используй его в SQL вместо `lower()`.
  - **Планировщик** вопрос→фильтр: новый Dify-ассистент **`usvo_filter_assistant.yml`**
    (`dify.usvo_ai.filter_app_key`, structured_output, temperature 0); без ключа — офлайн-эвристика
    `plan_offline_v2` (числа детей/«многодетные»/группа инвалидности/образование/склонения статусов).
    Старый `usvo_query_assistant.yml`/`planner_app_key` (плоский `QuerySpec`) — **легаси-фолбэк** (путь
    `legacy_build_query_context`, когда индекс не подан). `full_context_app_key` (вся база в контекст) —
    отдельный ВРЕМЕННЫЙ режим, не связан с этим пайплайном.
  - **Endpoint** `POST /api/web/usvo/query` (`web/usvo_query_router.py`, `require_user`, монтируется ×2 как
    остальные): `{question?|filter?, limit?}` → `{filter, total, aggregate, cards[], text}` — шаг «HTTP
    Request → backend», точка тестирования и для Dify-графа; `GET …/usvo/query/catalog` — справочник полей.
  Этот точный движок встраивается в `ai_chat.send_message` блоком «Точные данные по всей базе»
  (`build_query_context` → `format_db_result`); при `intent=semantic` блок пуст и ответ идёт обычным
  RAG-путём по базе знаний карточек (`usvo_knowledge.py` синхронизирует карточки/мету в датасет Dify).
- **Офлайн ИИ-помощники веб-кабинета** (детерминированно, без сети — намеренно): `web/insight.py`
  (`analyze_sentiment` — тональность обращения positive/neutral/anxious/aggressive по корням слов +
  `summarize` — «суть кратко») и `web/topics.py` (`classify_topic` — тематика обращения для таск-трекера
  и среза аналитики). Все три — правила/ключевые слова, формат полей совпадает с ветками Dify
  (`sentiment`/`summary` в `assistant.yml`), поэтому LLM можно подключить без правок фронтенда.

`data/schedule.yaml` — намеренно человекочитаемый, **часто меняемый** файл (здания + слоты),
перечитывается на каждый запрос без перезапуска.

`AGENTS.md` (в корне) — параллельное руководство для Codex с тем же содержанием, но **сокращённое и
сейчас отставшее** (например, всё ещё описывает 3-полевой JSON MAX-ветки вместо 5). При правке этого
CLAUDE.md решай, нужно ли синхронно обновить `AGENTS.md`.

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
