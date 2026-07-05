# Микросервис «Господдержка СВО»

Бэкенд для двух сценариев Dify-ассистента:

1. **Извлечение данных из Excel** (задание 1) — эндпоинт `POST /excel/extract` качественно
   превращает широкую разрежённую таблицу участников СВО в компактный текст, влезающий в лимит
   входа LLM **32k токенов**. Вызывается из Dify HTTP-узлом.
2. **Логика MAX-бота** (задание 2) — приём вебхуков MAX, эскалация неуверенных ответов операторам
   с inline-кнопкой «Ответить», запись ответа оператора обратно в базу знаний, предложение записи
   на приём после каждых 3 вопросов, выбор слота и отправка заявки операторам. Всё, что требует
   состояния (то, что Dify вести не может), реализовано здесь.

Также сервис раздаёт веб-интерфейс **«Контакт-центр»** на том же внешнем порту: разделы
«Обращения», «Персональные карточки УСВО», **«Чат с ИИ»**, «Заявления» и «Аналитика». Вход — экран в
стиле ЕСИА (`/login.html`) на базе **Ory Kratos** (demo-режим работает из коробки, см.
`deploy/kratos/`). В разделе «Заявления» появляются меры поддержки, которые MAX-бот
оформляет по фотографиям документов (визуальная модель → заполнение заявления →
подтверждение гражданином). Для небольшой операторской группы отдельный фронтенд-сервер
не нужен.

### Что нового в этой версии

- **Авторизация в стиле ЕСИА** (Ory Kratos, self-hosted; demo-fallback) — `app/auth/`,
  `static/login.html`, `deploy/kratos/`.
- **Раздел «Заявления»** (4-й, над аналитикой) — `/api/web/applications*`.
- **Сценарий «мера поддержки по фото»** в MAX-боте — `app/docs/`, `app/max/bot_logic.py`:
  фото → визуальная модель Dify → предложение меры → заполнение заявления
  (Dify-ассистент `document_assistant.yml`) → .docx на подтверждение → заявка в кабинет.
  Промты — в `../PROMPTS.md`. Без настроенного Dify работает офлайн-фолбэк.
- **Аналитика**: наполненные демо-данными дашборды + **тепловая карта** Ленинского
  городского округа с очагами обращений и заметкой ИИ.
- Над частью карточек УСВО — строка **«Поручение Главы округа»**.
- **Чат с ИИ по карточкам УСВО** — несколько изолированных по пользователю диалогов,
  три формата ответа, автоматическая синхронизация карточек и мета-агрегатов с Dify Dataset,
  ссылки из ответов на персональные карточки.

> Конфигурация — **в YAML-файле, а не в `.env`** (на сервере `.env` запрещён). См. `config.yaml`.

---

## 1. Структура проекта

```
microservice/
  app/
    main.py                # FastAPI: сборка приложения, lifespan, роутеры
    config.py              # загрузка/валидация config.yaml (pydantic)
    excel/
      extractor.py         # openpyxl → компактный текст (легенда столбцов + индексы)
      router.py            # POST /excel/extract  (защита X-Api-Key)
    max/
      router.py            # POST /max/webhook
      client.py            # обёртка MAX Bot API (сообщения, inline-кнопки, ответ на callback)
      dify_client.py       # вызов MAX-ветки Dify + запись в базу знаний (Dataset API)
      bot_logic.py         # вся логика бота (счётчик, эскалация, слоты, операторы)
      schedule.py          # чтение data/schedule.yaml
      store.py             # SQLite-состояние (stdlib sqlite3)
    common/
      health.py            # GET /health, GET /schedule
    web/
      router.py            # /api/web/* для веб-кабинета
      service.py           # обращения, карточки УСВО, аналитика, ИИ-подсказки
      ai_chat.py           # диалоги, история, форматы ответа и вызов Dify
      ai_chat_router.py    # /api/web/ai-chats*, admin rebuild базы знаний
      usvo_knowledge.py    # чанки карточек, мета-агрегаты, синхронизация Dataset
      static/              # SPA: index.html, app.js, styles.css
  data/
    schedule.yaml          # ЧАСТО МЕНЯЕМЫЙ файл: здания + слоты
    state.db               # SQLite (создаётся автоматически)
  config.yaml              # рабочая конфигурация (НЕ .env)
  config.example.yaml      # шаблон конфигурации с пояснениями
  requirements.txt
  README.md
```

---

## 2. Установка и запуск

```bash
cd microservice
python -m venv .venv
# Windows:  .venv\Scripts\activate
# Linux:    source .venv/bin/activate
pip install -r requirements.txt

# Настроить конфиг (один раз):
cp config.example.yaml config.yaml   # затем отредактировать значения

# Запуск:
uvicorn app.main:app --host 0.0.0.0 --port 8080
```

Для нескольких воркеров: `uvicorn app.main:app --workers 4 ...` или gunicorn с uvicorn-worker.
SQLite работает в режиме WAL, состояние общее для воркеров.

Веб-интерфейс: `http://<host>:8080/`.
Документация API (Swagger): `http://<host>:8080/docs`.

---

## 3. Конфигурация (`config.yaml`)

Путь к конфигу: `./config.yaml` по умолчанию; можно переопределить переменной окружения
`CONFIG_PATH=/path/to/config.yaml`. Все поля и комментарии — в `config.example.yaml`.

Ключевые поля:

| Поле | Назначение |
|------|-----------|
| `server.api_key` | Значение заголовка `X-Api-Key` для `/excel/extract` (тот же ключ в Dify HTTP-узле). |
| `excel.max_chars` | Бюджет символов на текст таблицы (защита от переполнения 32k токенов). 60000 ≈ 30k токенов. |
| `dify.base_url` | База Dify API, напр. `https://dify.t1v.scibox.tech/v1`. |
| `dify.max_app_key` | API-ключ Dify-приложения (`app-…`) — бот дёргает им MAX-ветку. |
| `dify.dataset.api_key` / `dataset_id` | Knowledge/Dataset API-ключ (`dataset-…`) и ID базы знаний для записи ответов операторов. |
| `dify.usvo_ai.app_key` | API-ключ отдельного Chat/Chatflow-приложения для раздела «Чат с ИИ». |
| `dify.usvo_ai.dataset.api_key` / `dataset_id` | Отдельная база знаний карточек УСВО; одна карточка синхронизируется отдельным документом. |
| `dify.usvo_ai.auto_sync` | Проверять хэш карточек и автоматически пересобирать знания перед запросом. |
| `max.api_base_url` | База MAX Bot API (по умолчанию `https://platform-api.max.ru`). |
| `max.bot_token` | Токен бота MAX. |
| `max.operator_chat_id` | ID чата операторов (эскалации и заявки на приём). |
| `max.questions_before_offer` | После скольких вопросов предлагать запись (по умолчанию 3). |
| `max.confidence_threshold` | Порог уверенности для эскалации (по умолчанию 0.6). |
| `storage.sqlite_path` | Путь к файлу SQLite. |
| `web.enabled` | Включить веб-кабинет на корневом пути `/`. |
| `web.usvo_xlsx` | Excel-таблица УСВО для карточек и аналитики. |
| `web.operators` | Список сотрудников в селекторе ответственного. |
| `web.seed_appeals` | Показывать обращения из таблицы УСВО, если реальных эскалаций ещё нет. |
| `schedule_file` | Путь к файлу расписания. |

---

## 4. Эндпоинты

### `POST /excel/extract` — задание 1
- Заголовок: `X-Api-Key: <server.api_key>`.
- Тело: `multipart/form-data`, поле `file` — `.xlsx`; необязательное поле `question`.
- Ответ:
  ```json
  {
    "text": "Легенда столбцов:\n[1] Дата обзвона\n...\n\nЗаписи ...\nЗапись 1: [1] 12.12.2025; [3] ...",
    "rows": 50, "total_rows": 50,
    "columns": ["Дата обзвона", "..."],
    "char_count": 43908, "token_estimate": 21954,
    "truncated": false,
    "question": "..."
  }
  ```
- **Как влезаем в 32k**: длинные названия столбцов выписываются в легенду **один раз**, в записях
  используется индекс `[N]`, пустые ячейки пропускаются. Пример 34×50 → ~44000 символов (~22k токенов)
  со всеми 50 записями. Если данных больше `max_chars` — лишние записи отбрасываются, `truncated=true`
  и в текст добавляется предупреждение.

### `POST /max/webhook` — задание 2
Принимает апдейты MAX Bot API (`message_created`, `message_callback`). Всегда отвечает 200, ошибки
логируются (чтобы MAX не зациклил ретраи). Логика — в `app/max/bot_logic.py`.

### `GET /schedule`
Текущее расписание (перечитывается из файла на каждый запрос). Удобно проверить правки.

### `GET /health`
`{"status": "ok"}`.

### `GET /` — веб-интерфейс «Контакт-центр»
Статическая SPA без сборки и внешних зависимостей. Раздаётся тем же uvicorn-процессом и тем же
внешним портом, что и API. Данные берёт через `/api/web/*`.

### `/api/web/*` — API веб-кабинета
- `GET /api/web/meta` — настройки интерфейса, список операторов, режим данных.
- `GET /api/web/appeals` — обращения оператору; `POST /api/web/appeals/{id}/draft` — черновик ответа;
  `POST /api/web/appeals/{id}/answer` — сохранить/отправить ответ.
- `GET /api/web/usvo` и `GET /api/web/usvo/{id}` — список и карточка УСВО.
- `GET /api/web/usvo/{id}/suggestions` — предложения по мерам поддержки.
- `GET|POST /api/web/ai-chats`, `GET|PATCH|DELETE /api/web/ai-chats/{id}` — диалоги
  текущего пользователя.
- `GET|POST /api/web/ai-chats/{id}/messages` — история и отправка сообщения в Dify.
- `POST /api/web/ai-knowledge/usvo/rebuild` — полная пересборка базы знаний УСВО
  (только администратор).
- `GET /api/web/applications` и `GET /api/web/applications/{id}` — заявления на меры поддержки.
- `POST /api/web/applications/{id}/approve|reject` — решение по заявлению.
- `GET /api/web/applications/{id}/docx` — скачать заполненное заявление в Word.
- `GET /api/web/analytics` — дашборд (включая тепловую карту и заметку ИИ).

Все `/api/web/*` (кроме авторизации) защищены сессией. Авторизация:
- `GET /api/web/auth/config` — режим входа и заголовок для логин-экрана;
- `POST /api/web/auth/login` `{identifier, password}` — вход (ставит cookie-сессию);
- `GET /api/web/auth/whoami` — текущий сотрудник; `POST /api/web/auth/logout` — выход.

Настройка отдельного Dify-ассистента, подключение Knowledge Retrieval и системный промпт:
`USVO_CHAT_PROMPT.md`.

---

## 5. Файл расписания `data/schedule.yaml`

Задуман как **часто меняемый** — правится вручную, перезапуск не нужен (читается на каждый запрос).
Каждое здание: `id`, `name`, `short` (для подписи кнопки), `address`, `work_hours`, `slots`
(список строк `"ЧЧ:ММ"`, один слот = один час). Пока показываются **все** слоты (логика занятости
не реализована — по заданию).

---

## 6. Интеграция с Dify (задание 1)

В Dify HTTP-узел настраивается так (см. `assistant.yml`, узел «HTTP извлечение Excel»):

- Метод: `POST`, URL: `https://<микросервис>/excel/extract`.
- Заголовки: `X-Api-Key: 112233` (как в `server.api_key`).
- Тело: `form-data`, поле `file` = файл из `sys.files` (тип `file`).
- Результат `body.text` подаётся в LLM как контекст таблицы.

**Передача файла.** Основной способ — `form-data` с файловой переменной Dify. Если ваша версия Dify
не умеет класть файл в тело HTTP-узла, используйте запасной вариант: передавать `sys.files[0].url`
строкой в JSON, а микросервис скачает файл сам (добавьте в `extract` приём поля `file_url` и
загрузку через httpx — точка расширения в `app/excel/router.py`).

---

## 7. Интеграция с MAX (задание 2)

### 7.1 Что заполнить в конфиге
`max.bot_token` (токен бота от @MasterBot), `max.operator_chat_id` (ID чата операторов),
`dify.max_app_key` (ключ MAX-приложения Dify), `dify.dataset.*` (база знаний для записи ответов).

### 7.2 Подключение бота (ВАЖНО — иначе «бот не отвечает»)

MAX сам по себе **не присылает** события в сервис, пока вы не подключите один из каналов
доставки. Используйте утилиту `maxctl.py` (из папки `microservice`, с заполненным `config.yaml`):

```bash
python maxctl.py me                  # 1) проверить токен — должно вернуть инфо о боте
python maxctl.py subscriptions       # 2) посмотреть текущие подписки
python maxctl.py subscribe https://<публичный-домен>/max/webhook   # 3) подписать вебхук
```

- **Вебхук** требует публичного адреса с **HTTPS и доверенным сертификатом** (требование MAX).
  Локальный `http://localhost` MAX не примет.
- **Нет публичного HTTPS?** Для отладки на localhost используйте long polling — события придут без
  вебхука, через ту же логику бота. Два варианта:
  - отдельным процессом: `python maxctl.py poll`;
  - **одним процессом с uvicorn**: поставьте в конфиге `max.polling: true` — тогда `uvicorn`
    одновременно обслуживает `/excel/extract` (задание 1) и сам опрашивает MAX в фоне.

  Webhook (`uvicorn` + `/max/webhook`) на localhost **не заработает** — MAX не может достучаться до
  вашей машины. Это не баг, а требование MAX к доставке (публичный HTTPS). Polling и активную
  подписку на вебхук одновременно держать нельзя.
- В логах сервиса при доставке событий появляется строка `MAX update получен: type=...`.
  Если её нет — событие не доходит (нет подписки / адрес недоступен MAX / неверный токен).

Бот должен **состоять в чате операторов** (`operator_chat_id`), иначе эскалации и заявки не дойдут.

> Замечание по API: используется актуальная база `https://platform-api.max.ru` и авторизация
> заголовком `Authorization: <token>`. Если ваш инстанс MAX работает на другом адресе/схеме —
> поменяйте `max.api_base_url` в конфиге и при необходимости заголовок в `app/max/client.py`.

### 7.3 Поток (реализован в `bot_logic.py`)
1. **Вопрос пользователя** → бот зовёт MAX-ветку Dify (`/chat-messages`, `inputs.channel="max"`),
   получает JSON `{answer, found_in_kb, confidence}`.
   - `found_in_kb=false` **или** `confidence < threshold` → **эскалация**: в чат операторов уходит
     вопрос + кнопка **«Ответить»**, пользователю — «передан оператору».
   - иначе пользователю отправляется `answer`.
2. После каждых `questions_before_offer` вопросов → «Не хотите записаться на приём?» + кнопки
   **«Записаться» / «Отказаться»**.
3. **Оператор жмёт «Ответить»** → следующее его сообщение в чате операторов уходит пользователю и
   **записывается в базу знаний** (Dataset API), эскалация закрывается.
4. **«Записаться»** → «Выберите удобное для вас время:» + кнопки слотов из `schedule.yaml`.
5. **Выбор слота** → в чат операторов уходит заявка: контакты пользователя, последние 3 вопроса,
   выбранное здание и время; пользователю — подтверждение.

### 7.4 Совместимость с MAX Bot API
Клиент в `app/max/client.py` написан под актуальную модель `platform-api.max.ru`: авторизация
заголовком `Authorization: <token>`, получатель сообщения (`chat_id`/`user_id`) передаётся
query-параметром в `POST /messages`, inline-клавиатура — attachment `inline_keyboard`,
ответ на callback — `POST /answers`. Если в вашем инстансе MAX отличаются пути или имена полей
апдейтов, правки локализованы в `client.py` (отправка/кнопки/ответ на callback) и в разборе апдейтов
в начале `bot_logic.py` (`_handle_message` / `_handle_callback`).

---

## 8. Запись ответа оператора в базу знаний

`app/max/dify_client.py::add_to_kb` вызывает Dify Dataset API:
`POST {base}/datasets/{dataset_id}/document/create-by-text`, заголовок
`Authorization: Bearer {dataset.api_key}`, тело — текст «Вопрос: … / Ответ: …». Документ
индексируется автоматически (`indexing_technique=high_quality`, `process_rule.mode=automatic`),
после чего ответ доступен в той же базе знаний при следующих вопросах.

---

## 9. Проверка локально

```bash
uvicorn app.main:app --port 8080
curl http://localhost:8080/health
curl http://localhost:8080/api/web/meta
curl http://localhost:8080/schedule
curl -X POST http://localhost:8080/excel/extract \
     -H "X-Api-Key: 112233" \
     -F "file=@../USVO_tablitsa_1_plus_49_synthetic_rows_dates_addresses_fixed.xlsx"
```

Ожидаемо: `rows=50`, `truncated=false`, `token_estimate < 32000`.
