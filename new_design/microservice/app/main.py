"""Точка входа FastAPI-микросервиса "Господдержка СВО"."""
from __future__ import annotations

import asyncio
import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles

from app.auth.employees import EmployeeStore
from app.auth.employees_router import router as settings_router
from app.auth.router import router as auth_router
from app.auth.service import AuthService
from app.common.health import router as common_router
from app.config import get_config
from app.docs.doc_ai import DocAI
from app.excel.router import router as excel_router
from app.max.bot_logic import MaxBot
from app.max.client import MaxClient
from app.max.dify_client import DifyClient
from app.max.polling import poll_loop
from app.max.router import router as max_router
from app.max.store import Store
from app.web.admin_router import router as admin_router
from app.web.ai_chat import AiChatService
from app.web.ai_chat_router import router as ai_chat_router
from app.web.measures_router import router as measures_router
from app.web.router import router as web_router
from app.web.service import WebService
from app.web.usvo import UsvoStore
from app.web.usvo_index import UsvoIndex
from app.web.usvo_knowledge import UsvoCardKnowledgeSyncService, UsvoCardsMetaService
from app.web.usvo_query_router import router as usvo_query_router

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    cfg = get_config()

    # общее состояние и клиенты создаются один раз на всё приложение
    store = Store(cfg.storage.sqlite_path)
    # демо-меры поддержки заводим при старте, повторный запуск дублей не создаёт
    from app.docs.measures import seed_demo_measures
    added = seed_demo_measures(store)
    if added:
        logging.getLogger("app").info("Заведено демо-мер поддержки: %d.", added)
    max_client = MaxClient(cfg.max.api_base_url, cfg.max.bot_token)
    dify = DifyClient(
        base_url=cfg.dify.base_url,
        app_key=cfg.dify.max_app_key,
        dataset_api_key=cfg.dify.dataset.api_key,
        dataset_id=cfg.dify.dataset.dataset_id,
    )
    doc_ai = DocAI(cfg)
    # учётки сотрудников лежат в отдельной БД
    employees = EmployeeStore(cfg.auth.employees_db)
    app.state.cfg = cfg
    app.state.store = store
    app.state.employees = employees
    app.state.auth = AuthService(cfg, employees)
    app.state.bot = MaxBot(cfg, store, max_client, dify, doc_ai)

    # веб-кабинет поднимается на том же порту, что и API
    if cfg.web.enabled:
        usvo_store = UsvoStore(cfg.web.usvo_xlsx, cfg.web.contact_stale_days)
        web = WebService(cfg, store, usvo_store, dify, max_client, employees)
        app.state.web = web
        usvo_dify = DifyClient(
            base_url=cfg.dify.base_url,
            app_key=cfg.dify.usvo_ai.app_key,
            dataset_api_key=cfg.dify.usvo_ai.dataset.api_key,
            dataset_id=cfg.dify.usvo_ai.dataset.dataset_id,
            timeout=cfg.dify.usvo_ai.timeout,
        )
        usvo_meta = UsvoCardsMetaService(web.all_usvo_records, web.usvo_last_updated)
        usvo_knowledge = UsvoCardKnowledgeSyncService(
            store,
            usvo_dify,
            web.all_usvo_records,
            usvo_meta,
            auto_sync=cfg.dify.usvo_ai.auto_sync,
        )
        web.attach_usvo_knowledge(usvo_knowledge)
        # SQLite-индекс карточек для точных фильтров и подсчётов
        usvo_index = UsvoIndex(
            web.all_usvo_records,
            web.usvo_last_updated,
            db_path=cfg.dify.usvo_ai.usvo_index_db,
        )
        app.state.usvo_index = usvo_index
        # планировщик фильтра: вопрос -> JSON-фильтр; без ключа работает офлайн-эвристика
        filter_dify = None
        if cfg.dify.usvo_ai.filter_app_key:
            filter_dify = DifyClient(
                base_url=cfg.dify.base_url,
                app_key=cfg.dify.usvo_ai.filter_app_key,
                dataset_api_key=cfg.dify.usvo_ai.dataset.api_key,
                dataset_id=cfg.dify.usvo_ai.dataset.dataset_id,
                timeout=cfg.dify.usvo_ai.timeout,
            )
        # временный режим: все карточки подаются модели целиком
        full_context_dify = None
        if cfg.dify.usvo_ai.full_context_app_key:
            full_context_dify = DifyClient(
                base_url=cfg.dify.base_url,
                app_key=cfg.dify.usvo_ai.full_context_app_key,
                dataset_api_key=cfg.dify.usvo_ai.dataset.api_key,
                dataset_id=cfg.dify.usvo_ai.dataset.dataset_id,
                timeout=cfg.dify.usvo_ai.timeout,
            )
        app.state.ai_chat = AiChatService(
            store,
            usvo_dify,
            web.all_usvo_records,
            usvo_meta,
            usvo_knowledge,
            max_history_messages=cfg.dify.usvo_ai.max_history_messages,
            planner=filter_dify,
            index=usvo_index,
            full_context_dify=full_context_dify,
            full_context_max_chars=cfg.dify.usvo_ai.full_context_max_chars,
        )
        if usvo_store.error:
            logging.getLogger("app").warning("Таблица УСВО: %s", usvo_store.error)
        logging.getLogger("app").info("Веб-кабинет включён: GET / (записей УСВО: %d).",
                                      len(usvo_store.all()))

    poll_task = None
    if cfg.max.polling:
        poll_task = asyncio.create_task(poll_loop(app.state.bot, max_client))
        logging.getLogger("app").info("Фоновый long polling MAX включён (max.polling=true).")

    # напоминания о просрочке включаются отдельным флагом, т.к. пишут в чат операторов
    sla_task = None
    if cfg.web.enabled and cfg.web.sla_reminders_enabled:
        sla_task = asyncio.create_task(_sla_reminder_loop(app.state.bot, cfg))
        logging.getLogger("app").info(
            "Фоновые SLA-напоминания включены (интервал %d мин).",
            cfg.web.sla_reminder_interval_minutes,
        )

    logging.getLogger("app").info("Сервис запущен. Конфиг загружен.")
    try:
        yield
    finally:
        for task in (poll_task, sla_task):
            if task is not None:
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass


async def _sla_reminder_loop(bot, cfg) -> None:
    """Периодически напоминает операторам о просроченных обращениях (best-effort)."""
    interval = max(1, int(cfg.web.sla_reminder_interval_minutes)) * 60
    while True:
        try:
            await bot.remind_overdue_escalations()
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001
            logging.getLogger("app").exception("Сбой SLA-напоминаний")
        await asyncio.sleep(interval)


app = FastAPI(title="Господдержка СВО — микросервис", version="1.0.0", lifespan=lifespan)

app.include_router(common_router)
app.include_router(excel_router)
app.include_router(max_router)
app.include_router(auth_router)
app.include_router(settings_router)
app.include_router(measures_router)
app.include_router(web_router)
app.include_router(admin_router)
app.include_router(ai_chat_router)
app.include_router(usvo_query_router)
app.include_router(auth_router, prefix="/application")
app.include_router(settings_router, prefix="/application")
app.include_router(measures_router, prefix="/application")
app.include_router(web_router, prefix="/application")
app.include_router(admin_router, prefix="/application")
app.include_router(ai_chat_router, prefix="/application")
app.include_router(usvo_query_router, prefix="/application")

# статика монтируется после роутеров, чтобы не перехватывать API-пути
_STATIC_DIR = os.path.join(os.path.dirname(__file__), "web", "static")
if get_config().web.enabled and os.path.isdir(_STATIC_DIR):
    _NO_CACHE_HEADERS = {
        "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
        "Pragma": "no-cache",
        "Expires": "0",
    }

    def _web_html(filename: str) -> FileResponse:
        return FileResponse(
            os.path.join(_STATIC_DIR, filename),
            headers=_NO_CACHE_HEADERS,
        )

    def _web_index() -> HTMLResponse:
        # index.html отдаём как есть: <base href> считает клиентский скрипт,
        # т.к. только браузер видит внешний префикс reverse-proxy
        with open(os.path.join(_STATIC_DIR, "index.html"), "r", encoding="utf-8") as f:
            html = f.read()
        return HTMLResponse(html, headers=_NO_CACHE_HEADERS)

    @app.get("/", include_in_schema=False)
    @app.get("/index.html", include_in_schema=False)
    @app.get("/login", include_in_schema=False)
    @app.get("/login.html", include_in_schema=False)
    @app.get("/cards", include_in_schema=False)
    @app.get("/ai-chat", include_in_schema=False)
    @app.get("/applications", include_in_schema=False)
    @app.get("/analytics", include_in_schema=False)
    @app.get("/broadcast", include_in_schema=False)
    @app.get("/audit", include_in_schema=False)
    @app.get("/settings", include_in_schema=False)
    @app.get("/application", include_in_schema=False)
    @app.get("/application/", include_in_schema=False)
    @app.get("/application/index.html", include_in_schema=False)
    @app.get("/application/login", include_in_schema=False)
    @app.get("/application/login.html", include_in_schema=False)
    @app.get("/application/cards", include_in_schema=False)
    @app.get("/application/ai-chat", include_in_schema=False)
    @app.get("/application/applications", include_in_schema=False)
    @app.get("/application/analytics", include_in_schema=False)
    @app.get("/application/broadcast", include_in_schema=False)
    @app.get("/application/audit", include_in_schema=False)
    @app.get("/application/settings", include_in_schema=False)
    async def web_index():
        return _web_index()

    @app.get("/usvo/cards/{rec_id}", include_in_schema=False)
    @app.get("/application/usvo/cards/{rec_id}", include_in_schema=False)
    async def web_usvo_card(rec_id: int):
        return _web_index()

    app.mount("/application", StaticFiles(directory=_STATIC_DIR, html=True), name="web-application")
    app.mount("/", StaticFiles(directory=_STATIC_DIR, html=True), name="web-static")
