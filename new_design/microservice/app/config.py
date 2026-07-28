"""Конфигурация сервиса. Грузится из YAML (config.yaml или CONFIG_PATH)."""
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
    """Провайдер распознавания документов по фото: dify или openrouter."""

    provider: str = "dify"  # dify | openrouter
    openrouter_base_url: str = "https://openrouter.ai/api/v1"
    openrouter_api_key: str = ""
    openrouter_model: str = ""  # напр. "google/gemini-2.5-flash"
    temperature: float = 0.1
    timeout: float = 90.0


class UsvoAiCfg(BaseModel):
    """Dify-приложение и датасет для «Чата с ИИ» по карточкам УСВО."""

    app_key: str = ""
    dataset: DatasetCfg = Field(default_factory=DatasetCfg)
    timeout: float = 90.0
    max_history_messages: int = 30
    auto_sync: bool = True
    # устаревший планировщик запросов, работает только если filter_app_key не задан
    planner_app_key: str = ""
    # планировщик фильтра: вопрос -> JSON-фильтр, исполняется в SQLite-индексе
    filter_app_key: str = ""
    # производный индекс карточек, пересобирается сам при изменении данных
    usvo_index_db: str = "./data/usvo_index.db"
    # временный режим: вся база карточек подаётся модели целиком, без RAG
    full_context_app_key: str = ""
    # лимит символов на блок с базой карточек в промпте
    full_context_max_chars: int = 45000


class DifyCfg(BaseModel):
    base_url: str = "https://dify.t1v.scibox.tech/v1"
    max_app_key: str = ""
    # единый ассистент документов (чтение/извлечение полей/перевод/распознавание)
    documents_app_key: str = ""
    # старый vision-воркфлоу, нужен только если documents_app_key пуст
    doc_vision_app_key: str = ""
    # ассистент, который заполняет заявление и собирает .docx
    doc_fill_app_key: str = ""
    # подбор меры поддержки по описанию ситуации
    measure_app_key: str = ""
    # сюда кабинет кладёт загруженные DOCX-шаблоны мер
    measure_templates_dir: str = "./data/measure_templates"
    # нормализация «Истории взаимодействия» в события; пусто — офлайн-разбор
    history_app_key: str = ""
    # шаблон заявления, отдаётся в Dify при генерации документа
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
    # порог фильтра «не по теме»: при меньшей уверенности запрос не отсеиваем
    topic_threshold: float = 0.5
    # long polling вместо вебхука; вместе с активной подпиской не включать
    polling: bool = False


class StorageCfg(BaseModel):
    sqlite_path: str = "./data/state.db"


class AuthCfg(BaseModel):
    """Авторизация кабинета: demo (локальный список), kratos или external (SSO)."""

    enabled: bool = True
    mode: str = "demo"  # demo | kratos | external
    # имя/роль, которые показываем при mode=external
    external_name: str = "Пользователь платформы"
    external_role: str = "Оператор"
    # публичный API Kratos (для mode=kratos)
    kratos_public_url: str = "http://localhost:4433"
    # секрет подписи cookie-сессии, на проде обязательно сменить
    session_secret: str = "change-me-please-set-a-long-random-secret"
    session_ttl_hours: int = 12
    # отдельная БД учёток сотрудников (создаёт админ в «Настройках»)
    employees_db: str = "./data/employees.db"
    # демо-пользователи: логин -> пароль, имя, роль
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
        # старое имя ключа "users" тоже принимаем
        if isinstance(data, dict) and "demo_users" not in data and "users" in data:
            data = dict(data)
            data["demo_users"] = data["users"]
        return data


class WebAiCfg(BaseModel):
    """ИИ для черновиков ответов: local (шаблон), dify или openai_compatible."""

    provider: str = "local"
    dify_base_url: str = ""
    dify_app_key: str = ""
    # auto: сначала /chat-messages, потом /completion-messages
    dify_mode: str = "auto"
    # имя input-переменной в completion-приложении Dify
    dify_input_variable: str = "query"
    openai_base_url: str = ""
    openai_api_key: str = ""
    openai_model: str = ""
    temperature: float = 0.2
    timeout: float = 60.0


class WebCfg(BaseModel):
    """Веб-кабинет контакт-центра. Раздаётся тем же uvicorn, что и API."""

    enabled: bool = True
    title: str = "Контакт-центр — Господдержка СВО"
    # исходная Excel-таблица УСВО
    usvo_xlsx: str = "../USVO_tablitsa_1_plus_49_synthetic_rows_dates_addresses_fixed.xlsx"
    # текст базы знаний для офлайн-подсказок, когда Dify не настроен
    kb_text: str = "../Информация для базы знаний.txt"
    # без контакта дольше этого срока — попадает в аналитику
    contact_stale_days: int = 90
    # регламентный срок ответа на обращение, календарных дней
    sla_business_days: int = 3
    # напоминания операторам о просрочке (фон, нужен рабочий бот); по умолчанию выкл
    sla_reminders_enabled: bool = False
    sla_reminder_interval_minutes: int = 60
    # список операторов для назначения ответственного
    operators: list[str] = Field(
        default_factory=lambda: ["Оператор администрации"]
    )
    ai: WebAiCfg = Field(default_factory=WebAiCfg)
    # если реальных обращений ещё нет — показать синтетические из таблицы УСВО
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
    """Конфиг читается один раз и кэшируется."""
    return load_config()
