"""Загрузка и валидация конфигурации из YAML-файла (вместо запрещённого .env).

Путь к конфигу выбирается так (по приоритету):
  1. аргумент load_config(path=...)
  2. переменная окружения CONFIG_PATH
  3. ./config.yaml (по умолчанию)
"""
from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

import yaml
from pydantic import BaseModel, Field, model_validator


class ServerCfg(BaseModel):
    host: str = "0.0.0.0"
    port: int = 8080
    api_key: str = ""  # X-Api-Key для /excel/extract


class ExcelCfg(BaseModel):
    max_chars: int = 90000
    drop_empty_cells: bool = True


class DatasetCfg(BaseModel):
    api_key: str = ""
    dataset_id: str = ""


class DocVisionCfg(BaseModel):
    """Провайдер для шага ① (распознавание документов по фото).

    provider:
      - dify       : Dify-воркфлоу (vision_assistant.yml), промты внутри ассистента.
                     Требует заполненного dify.doc_vision_app_key.
      - openrouter : Прямой вызов vision-модели через OpenRouter API
                     (POST /chat/completions). Промты передаются из кода.
    """

    provider: str = "dify"  # dify | openrouter
    openrouter_base_url: str = "https://openrouter.ai/api/v1"
    openrouter_api_key: str = ""
    openrouter_model: str = ""  # напр. "google/gemini-2.5-flash"
    temperature: float = 0.1
    timeout: float = 90.0


class UsvoAiCfg(BaseModel):
    """Отдельное Dify-приложение и Knowledge Dataset для «Чата с ИИ» по УСВО."""

    app_key: str = ""
    dataset: DatasetCfg = Field(default_factory=DatasetCfg)
    timeout: float = 90.0
    max_history_messages: int = 30
    # Необязательный Dify-ассистент-планировщик запросов (usvo_query_assistant.yml,
    # structured_output QuerySpec): превращает вопрос в фильтр/агрегат для точного
    # подсчёта по всем карточкам. Пусто → офлайн-эвристика разбора вопроса.
    # ЛЕГАСИ (плоский QuerySpec): используется только если filter_app_key не задан и
    # индекс не подан. Новый путь — filter_app_key + usvo_filter_assistant.yml.
    planner_app_key: str = ""
    # Новый Dify-ассистент-планировщик фильтра (usvo_filter_assistant.yml, structured_output
    # DSL): вопрос → JSON-фильтр с вложенными AND/OR, числовыми сравнениями и доступом к
    # любому полю карточки (attr:<key>). Микросервис валидирует фильтр и исполняет его в
    # SQLite-индексе карточек (app/web/usvo_index.py). Пусто → офлайн-эвристика (plan_offline_v2).
    filter_app_key: str = ""
    # Файл производного SQLite-индекса карточек УСВО (полностью derived, пересобирается
    # при изменении набора карточек; authoritative state.db не засоряется).
    usvo_index_db: str = "./data/usvo_index.db"
    # ВРЕМЕННОЕ решение: отдельный Dify-ассистент (usvo_full_context_assistant.yml),
    # которому микросервис подаёт в контекст СРАЗУ ВСЕ карточки УСВО целиком (без RAG/
    # knowledge-retrieval и без детерминированного движка). Если ключ задан — «Чат с ИИ»
    # работает в режиме full_context: модель сама считает/перечисляет/агрегирует по всем
    # карточкам. Пусто → обычный режим (app_key + база знаний + движок запросов).
    full_context_app_key: str = ""
    # Бюджет символов на блок «Полная база карточек УСВО» в промпте (≈ половина в токенах
    # для кириллицы). Держим заметно ниже окна модели, оставляя место под систему/историю/
    # ответ. При превышении список карточек усекается с пометкой.
    full_context_max_chars: int = 45000


class DifyCfg(BaseModel):
    base_url: str = "https://dify.t1v.scibox.tech/v1"
    max_app_key: str = ""
    # Единый Dify-ассистент работы с документами (documents_assistant.yml): один
    # advanced-chat с роутером по входу `task` на 4 ветки — read (чтение текстом),
    # extract (извлечение полей по списку), translate (перевод подписей полей),
    # recognize (распознавание ЖКУ-заявления). ПРЕДПОЧТИТЕЛЬНЫЙ способ: заменяет и
    # vision_assistant.yml, и прямые вызовы OpenRouter. Пусто/плейсхолдер → старая
    # логика (doc_vision_app_key / vision.provider). См. CLAUDE.md, PROMPTS.md.
    documents_app_key: str = ""
    # Отдельный Dify-воркфлоу для анализа фотографий документов (визуальная модель).
    # Легаси: используется только если documents_app_key пуст и vision.provider=dify.
    doc_vision_app_key: str = ""
    # Отдельный Dify-ассистент, заполняющий заявление и собирающий .docx по шаблону
    # (document_assistant.yml: узел generate_docx). Сам файл формируется в Dify.
    doc_fill_app_key: str = ""
    # Отдельный Dify-ассистент, выбирающий подходящую меру поддержки по описанию
    # ситуации гражданина (measure_assistant.yml, structured_output). Подключается
    # к базе знаний с описанием активных мер. Пусто → офлайн-подбор по ключевым словам.
    measure_app_key: str = ""
    # Каталог, куда веб-кабинет сохраняет загруженные DOCX-шаблоны мер поддержки.
    measure_templates_dir: str = "./data/measure_templates"
    # Отдельный Dify-ассистент, нормализующий свободный текст «Истории взаимодействия»
    # карточки УСВО в JSON-события для красивого вывода (history_assistant.yml,
    # подключается к базе знаний data/history_styles.md). Пусто → офлайн-нормализатор.
    history_app_key: str = ""
    # DOCX-шаблон заявления, который микросервис загружает в Dify и передаёт в
    # переменную `template` узла Start. Положите сюда новый шаблон, чтобы он
    # использовался при создании. Путь — относительно рабочего каталога microservice.
    doc_template_path: str = "./data/Zayavlenie_Shablon.docx"
    dataset: DatasetCfg = Field(default_factory=DatasetCfg)
    usvo_ai: UsvoAiCfg = Field(default_factory=UsvoAiCfg)
    vision: DocVisionCfg = Field(default_factory=DocVisionCfg)


class MaxCfg(BaseModel):
    api_base_url: str = "https://platform-api.max.ru"
    bot_token: str = ""
    operator_chat_id: str = ""
    questions_before_offer: int = 2
    confidence_threshold: float = 0.6
    # Порог тематической релевантности (0..1): запросы не по теме господдержки СВО
    # с уверенностью ниже порога отклоняются базовым сообщением и НЕ доходят до
    # операторов. См. поля on_topic / topic_confidence структурированного ответа Dify.
    topic_threshold: float = 0.5
    # true → uvicorn сам опрашивает MAX в фоне (long polling), вебхук не нужен.
    # Не включайте одновременно с активной подпиской на вебхук.
    polling: bool = False


class StorageCfg(BaseModel):
    sqlite_path: str = "./data/state.db"


class AuthCfg(BaseModel):
    """Авторизация веб-кабинета в стиле ЕСИА на базе Ory Kratos.

    Всё разворачивается на сервере (см. deploy/kratos/docker-compose.yml).

    mode:
      - demo   : вход проверяется по локальному списку `users` (работает сразу,
                 без поднятого Kratos — удобно для демонстрации и offline);
      - kratos : учётные данные проверяются через Kratos Identity API
                 (self-service login flow, `kratos_public_url`);
      - external: авторизацию выполняет внешний reverse proxy / SSO. Собственная
                  cookie-сессия кабинета не требуется.
    """

    enabled: bool = True
    mode: str = "demo"  # demo | kratos | external
    # Отображаемая учётная запись для режима external. Сам доступ к сервису
    # должен быть закрыт внешним SSO/reverse proxy.
    external_name: str = "Пользователь платформы"
    external_role: str = "Оператор"
    # Адрес публичного API Kratos (для mode=kratos). На сервере — http://kratos:4433.
    kratos_public_url: str = "http://localhost:4433"
    # Секрет для подписи cookie-сессии кабинета (HMAC). СМЕНИТЕ на проде.
    session_secret: str = "change-me-please-set-a-long-random-secret"
    # Время жизни сессии в часах.
    session_ttl_hours: int = 12
    # Отдельная БД для учётных записей сотрудников, которые создаёт администратор
    # в разделе «Настройки» (роль «Сотрудник»). Не смешивается с состоянием бота.
    employees_db: str = "./data/employees.db"
    # Демо-пользователи: identifier (телефон/email/логин) → пароль и отображаемое имя.
    demo_users: list[dict] = Field(
        default_factory=lambda: [
            {"identifier": "operator@mosreg.ru", "password": "kontakt2026",
             "name": "Иванова О. П.", "role": "Сотрудник"},
            {"identifier": "admin@mosreg.ru", "password": "admin2026",
             "name": "Петров С. А.", "role": "Администратор"},
        ]
    )

    @model_validator(mode="before")
    @classmethod
    def _accept_legacy_users_key(cls, data):
        if isinstance(data, dict) and "demo_users" not in data and "users" in data:
            data = dict(data)
            data["demo_users"] = data["users"]
        return data


class WebAiCfg(BaseModel):
    """ИИ для веб-черновиков и заметок.

    provider:
      - local: без внешнего ИИ, только шаблон;
      - dify: отдельное Dify-приложение, например простое LLM-приложение;
      - openai_compatible: прямой вызов модели через /chat/completions.
    """

    provider: str = "local"
    dify_base_url: str = ""
    dify_app_key: str = ""
    # auto: сначала /chat-messages, затем /completion-messages.
    # chat: только /chat-messages. completion: только /completion-messages.
    dify_mode: str = "auto"
    # Имя input-переменной в Dify completion-приложении.
    dify_input_variable: str = "query"
    openai_base_url: str = ""
    openai_api_key: str = ""
    openai_model: str = ""
    temperature: float = 0.2
    timeout: float = 60.0


class WebCfg(BaseModel):
    """Веб-интерфейс «Контакт-центр» (кабинет оператора/администрации).

    Раздаётся тем же uvicorn на том же внешнем порту, что и API — отдельный
    сервер не нужен. Регистрация не требуется (по заданию)."""

    enabled: bool = True
    title: str = "Контакт-центр — Господдержка СВО"
    # Путь к Excel-таблице УСВО (источник персональных карточек и аналитики).
    usvo_xlsx: str = "../USVO_tablitsa_1_plus_49_synthetic_rows_dates_addresses_fixed.xlsx"
    # Текст базы знаний — используется для офлайн-предложений по льготам,
    # если Dify не настроен.
    kb_text: str = "../Информация для базы знаний.txt"
    # УСВО, с которым не было контакта дольше этого срока, попадает в аналитику.
    contact_stale_days: int = 90
    # Регламентный срок обработки обращения гражданина, В КАЛЕНДАРНЫХ ДНЯХ (все дни
    # подряд, включая выходные). По истечении обращение помечается «Просрочено»
    # в кабинете (см. app/web/sla.py).
    sla_business_days: int = 3
    # Повторные напоминания операторам о просроченных обращениях в чат операторов
    # (фоновая задача, требует настроенного MAX-бота). По умолчанию выключено, чтобы
    # не слать сообщения без явного согласия. Интервал — в минутах.
    sla_reminders_enabled: bool = False
    sla_reminder_interval_minutes: int = 60
    # Список операторов для назначения ответственного по обращению.
    operators: list[str] = Field(
        default_factory=lambda: ["Оператор администрации"]
    )
    ai: WebAiCfg = Field(default_factory=WebAiCfg)
    # Если в БД ещё нет реальных обращений (бот не запускался) — показать
    # обращения, синтезированные из таблицы УСВО.
    seed_appeals: bool = True


class Config(BaseModel):
    server: ServerCfg = Field(default_factory=ServerCfg)
    excel: ExcelCfg = Field(default_factory=ExcelCfg)
    dify: DifyCfg = Field(default_factory=DifyCfg)
    max: MaxCfg = Field(default_factory=MaxCfg)
    storage: StorageCfg = Field(default_factory=StorageCfg)
    web: WebCfg = Field(default_factory=WebCfg)
    auth: AuthCfg = Field(default_factory=AuthCfg)
    schedule_file: str = "./data/schedule.yaml"


def load_config(path: str | None = None) -> Config:
    path = path or os.environ.get("CONFIG_PATH")
    if path is None:
        path = "config.yaml"
        if not os.path.exists(path):
            bundled = Path(__file__).resolve().parents[1] / "config.yaml"
            if bundled.exists():
                path = str(bundled)
    if not os.path.exists(path):
        raise FileNotFoundError(
            f"Не найден файл конфигурации: {path}. "
            f"Скопируйте config.example.yaml в config.yaml и заполните значения."
        )
    with open(path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    return Config(**data)


@lru_cache(maxsize=1)
def get_config() -> Config:
    """Кэшированный конфиг для всего приложения (читается один раз при старте)."""
    return load_config()
