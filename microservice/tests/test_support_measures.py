"""Тесты сценария «Меры поддержки» (без сети, детерминированно).

Запуск из каталога microservice:
    python -m pytest tests/test_support_measures.py -q
"""
from __future__ import annotations

import asyncio
import io
import json
import os
import sys
import tempfile
import zipfile

# Делает пакет app импортируемым при запуске pytest из каталога microservice.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.common.docx import Paragraph, build_docx  # noqa: E402
from app.config import Config  # noqa: E402
from app.docs import measures as measures_mod  # noqa: E402
from app.docs.templates import (  # noqa: E402
    check_required,
    extract_placeholders,
    fill_template_docx,
)
from app.max.store import Store  # noqa: E402


def _docx_text(docx_bytes: bytes) -> str:
    with zipfile.ZipFile(io.BytesIO(docx_bytes)) as z:
        return z.read("word/document.xml").decode("utf-8")


def _make_template() -> bytes:
    return build_docx([
        Paragraph("Заявление"),
        Paragraph("ФИО: {{full_name}}"),
        Paragraph("Паспорт: {{passport_series}} {{passport_number}}"),
        Paragraph("Телефон: {{phone_number}}"),
    ])


# ---- templates.py ----------------------------------------------------------

def test_extract_placeholders():
    keys = extract_placeholders(_make_template())
    assert keys == ["full_name", "passport_series", "passport_number", "phone_number"]


def test_extract_placeholders_bad_bytes():
    assert extract_placeholders(b"not a docx") == []


def test_fill_template_roundtrip():
    tpl = _make_template()
    out = fill_template_docx(tpl, {
        "full_name": "Иванов Иван Иванович",
        "passport_series": "4614",
        "passport_number": "778512",
        "phone_number": "+7 900 000-00-00",
    })
    text = _docx_text(out)
    assert "Иванов Иван Иванович" in text
    assert "778512" in text
    # Все плейсхолдеры подставлены — в готовом файле их не осталось.
    assert extract_placeholders(out) == []


def test_fill_template_xml_escaping():
    tpl = build_docx([Paragraph("Орг: {{org}}")])
    out = fill_template_docx(tpl, {"org": 'ООО "Ромашка" & Ко <тест>'})
    text = _docx_text(out)
    assert "&amp;" in text and "&lt;" in text  # спецсимволы экранированы
    assert "{{org}}" not in text


def test_check_required():
    res = check_required(
        ["full_name", "phone_number", "bank_account"],
        {"full_name": "Иванов И. И.", "phone_number": "  ", "bank_account": "40817"},
    )
    assert res["filled_fields"] == {"full_name": "Иванов И. И.", "bank_account": "40817"}
    assert res["missing_fields"] == ["phone_number"]


# ---- measures.py -----------------------------------------------------------

def test_build_measure_data_normalizes():
    data = measures_mod.build_measure_data(
        documents=["Паспорт", "", "СНИЛС"],
        placeholders=[{"key": "full_name", "label": "ФИО"}, {"key": "full_name"}, {"key": ""}],
        llm_hint="единовременная выплата",
    )
    assert [d["title"] for d in data["documents"]] == ["Паспорт", "СНИЛС"]
    # Дубликат ключа и пустой ключ отброшены, label у второго проставлен по ключу.
    assert data["placeholders"] == [{"key": "full_name", "label": "ФИО"}]


def test_match_measure_offline():
    measures = [
        {"id": 1, "title": "Компенсация расходов на ЖКУ",
         "description": "оплата коммунальных услуг", "llm_hint": "жку коммуналка"},
        {"id": 2, "title": "Единовременная выплата при ранении",
         "description": "денежная выплата", "llm_hint": "ранение госпиталь выплата"},
    ]
    res = measures_mod.match_measure_offline("получил ранение, положена выплата?", measures)
    assert res["found"] is True
    assert res["measure_id"] == 2

    none = measures_mod.match_measure_offline("какая сегодня погода", measures)
    assert none["found"] is False


def test_measure_kb_text_contains_titles():
    measures = [{"id": 7, "title": "Путёвка в санаторий", "description": "лечение",
                 "llm_hint": "", "documents": [{"title": "Паспорт"}], "placeholders": []}]
    text = measures_mod.measure_kb_text(measures)
    assert "id 7" in text and "Путёвка в санаторий" in text and "Паспорт" in text


# ---- store.py --------------------------------------------------------------

def _temp_store() -> Store:
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    return Store(path)


def test_support_measure_crud():
    store = _temp_store()
    mid = store.add_support_measure({
        "title": "Тестовая мера",
        "description": "описание",
        "data": json.dumps({"documents": [{"title": "Паспорт", "sort_order": 0}],
                            "placeholders": [{"key": "full_name", "label": "ФИО"}]}),
        "active": True,
    })
    assert mid > 0
    row = store.get_support_measure(mid)
    md = measures_mod.measure_to_dict(row)
    assert md["title"] == "Тестовая мера"
    assert md["documents"][0]["title"] == "Паспорт"
    assert md["active"] is True

    # деактивация исключает из active_only-выборки
    store.set_support_measure_active(mid, False)
    assert store.list_support_measures(active_only=True) == []
    assert len(store.list_support_measures()) == 1

    assert store.delete_support_measure(mid) is True
    assert store.get_support_measure(mid) is None


def test_measure_application_flow_helpers():
    store = _temp_store()
    flow = {"measure_id": 1, "stage": "waiting_document", "doc_index": 0, "docs": []}
    app_id = store.create_measure_application(
        "u1", "100", "Иван", "", 1, "Мера", json.dumps(flow), status="waiting_document",
    )
    active = store.get_active_flow("u1")
    assert active is not None and int(active["id"]) == app_id

    # после подачи заявка больше не считается активным флоу
    store.set_application_status(app_id, "submitted")
    assert store.get_active_flow("u1") is None


# ---- интеграция бота (фейковые клиенты) ------------------------------------

class FakeMax:
    def __init__(self):
        self.messages: list[dict] = []
        self.documents: list[dict] = []
        self._mid = 0

    async def send_message(self, text, chat_id=None, user_id=None, keyboard_rows=None):
        self._mid += 1
        self.messages.append({"text": text, "chat_id": chat_id, "rows": keyboard_rows})
        return {"message": {"body": {"mid": f"m{self._mid}"}}}

    async def send_document(self, file_bytes, filename, chat_id=None, user_id=None,
                            caption="", keyboard_rows=None):
        self.documents.append({"filename": filename, "caption": caption})
        return {"ok": True}

    async def delete_message(self, message_id):
        return {"ok": True}

    async def answer_callback(self, callback_id, notification=None):
        return {"ok": True}


class FakeDocAI:
    def __init__(self, extract_result):
        self._extract = extract_result

    async def extract_fields(self, image_urls, fields, user_key):
        return self._extract

    async def translate_labels(self, fields):
        from app.docs.doc_ai import _offline_ru_label
        return [_offline_ru_label(f) for f in fields]

    async def generate_measure_docx(self, template_path, values, user_key):
        return {"bytes": b"PK\x03\x04 fake docx", "source": "local"}

    async def select_measure(self, query, measures, user_key):
        return None


def _bot(store, fake_max, fake_doc):
    from app.max.bot_logic import MaxBot
    cfg = Config()
    return MaxBot(cfg, store, fake_max, dify=None, doc_ai=fake_doc)


def _photo_msg(url="http://x/1.jpg"):
    return {
        "sender": {"user_id": "u1", "name": "Иван"},
        "recipient": {"chat_id": "100"},
        "body": {"text": "", "attachments": [{"type": "image", "payload": {"url": url}}]},
    }


def _text_msg(text):
    return {
        "sender": {"user_id": "u1", "name": "Иван"},
        "recipient": {"chat_id": "100"},
        "body": {"text": text},
    }


def test_full_measure_flow_with_missing_field():
    store = _temp_store()
    # Мера с двумя документами и двумя полями шаблона.
    mid = store.add_support_measure({
        "title": "Компенсация расходов",
        "description": "",
        "data": json.dumps({
            "documents": [{"title": "Паспорт", "sort_order": 0},
                          {"title": "Реквизиты счёта", "sort_order": 1}],
            "placeholders": [{"key": "full_name", "label": "ФИО"},
                             {"key": "phone_number", "label": "Телефон"}],
        }),
        "template_path": "",
        "active": True,
    })
    # Распознаётся ФИО, телефон — нет (уйдёт в дозапрос).
    fake_doc = FakeDocAI({"filled_fields": {"full_name": "Иванов Иван"},
                          "missing_fields": ["phone_number"]})
    fake_max = FakeMax()
    bot = _bot(store, fake_max, fake_doc)

    async def scenario():
        # 1. выбор меры
        await bot._start_measure_flow("u1", "100", "Иван", "", mid)
        row = store.get_active_flow("u1")
        assert row is not None
        assert row["status"] == "waiting_document"
        # запрошен первый документ
        assert any("Паспорт" in m["text"] for m in fake_max.messages)

        # 2. фото первого документа → запрос второго
        await bot._handle_message(_photo_msg("http://x/passport.jpg"))
        assert any("Реквизиты счёта" in m["text"] for m in fake_max.messages)

        # 3. фото второго → распознавание → не хватает телефона → дозапрос
        await bot._handle_message(_photo_msg("http://x/bank.jpg"))
        assert store.get_application(int(row["id"]))["status"] == "waiting_missing_fields"
        assert any("Телефон" in m["text"] for m in fake_max.messages)

        # 4. не фото при сборе документов состояние не сбрасывает — проверим отдельно ниже.

        # 5. пользователь дописывает телефон одним сообщением → генерация + оффер
        await bot._handle_message(_text_msg("+7 900 123-45-67"))
        app = store.get_application(int(row["id"]))
        assert app["status"] == "ready_for_confirmation"
        assert len(fake_max.documents) == 1  # заявление .docx отправлено
        data = json.loads(app["data"])
        assert data["fields"]["phone_number"] == "+7 900 123-45-67"

        # 6. подтверждение → submitted + уведомление операторов
        await bot._submit_measure_application("u1", "100", "Иван", "", int(row["id"]))
        assert store.get_application(int(row["id"]))["status"] == "submitted"

    asyncio.run(scenario())


def test_non_photo_during_collection_keeps_state():
    store = _temp_store()
    mid = store.add_support_measure({
        "title": "Мера",
        "data": json.dumps({"documents": [{"title": "Паспорт", "sort_order": 0}],
                            "placeholders": [{"key": "full_name", "label": "ФИО"}]}),
        "active": True,
    })
    fake_max = FakeMax()
    bot = _bot(store, fake_max, FakeDocAI({"filled_fields": {}, "missing_fields": ["full_name"]}))

    async def scenario():
        await bot._start_measure_flow("u1", "100", "Иван", "", mid)
        row = store.get_active_flow("u1")
        # прислал текст вместо фото → напоминание, состояние НЕ сброшено
        await bot._handle_message(_text_msg("привет"))
        assert any("фото документа" in m["text"].lower() for m in fake_max.messages)
        assert store.get_application(int(row["id"]))["status"] == "waiting_document"

    asyncio.run(scenario())


def _callback(action: dict) -> dict:
    return {
        "update_type": "message_callback",
        "callback": {"callback_id": "c", "user": {"user_id": "u1", "name": "Анна"},
                     "payload": json.dumps(action)},
        "message": {"recipient": {"chat_id": "100"}, "body": {"mid": "mx"}},
    }


def _contact_msg(tel: str = "+79031234567") -> dict:
    return {"sender": {"user_id": "u1", "name": "Анна"}, "recipient": {"chat_id": "100"},
            "body": {"text": "", "attachments": [{"type": "contact", "payload": {"tel": tel}}]}}


def test_guided_measure_flow_registers_application():
    """Сценарий из макета: тема → опрос → профиль → телефон → документы → заявка."""
    store = _temp_store()
    mid = store.add_support_measure({
        "title": "Бесплатное питание в школе",
        "data": json.dumps({"documents": [{"title": "Паспорт", "sort_order": 0}],
                            "placeholders": [{"key": "full_name", "label": "ФИО"}]}),
        "active": True,
    })
    fake_max = FakeMax()
    bot = _bot(store, fake_max, FakeDocAI({"filled_fields": {"full_name": "Иванова Анна"},
                                           "missing_fields": []}))

    async def scenario():
        # экран 1 → тема «Меры поддержки» запускает гид-опрос
        await bot.handle_update(_callback({"a": "topic", "t": "measures"}))
        assert store.get_bot_flow("u1")["stage"] == "collect_profile"
        # экран 2: родство кнопкой, регион — текстом
        await bot.handle_update(_callback({"a": "rel", "v": "Супруга"}))
        await bot._handle_message(_text_msg("Да, прописана в Видном"))
        # экран 3: сводка профиля с бейджами, ждём телефон
        assert store.get_bot_flow("u1")["stage"] == "await_phone"
        assert any("🟢 Подтверждено" in m["text"] for m in fake_max.messages)
        # телефон контактом → единственная мера → сбор документов (гид-опрос закрыт)
        await bot._handle_message(_contact_msg())
        assert store.get_user("u1")["phone"].startswith("+7")
        row = store.get_active_flow("u1")
        assert row is not None and store.get_bot_flow("u1") is None
        # экран 4-5: фото документа → распознавание → регистрация заявки
        await bot._handle_message(_photo_msg("http://x/passport.jpg"))
        app = store.get_application(int(row["id"]))
        assert app["status"] == "submitted"
        assert any("заявка зарегистрирована" in m["text"] for m in fake_max.messages)
        assert any("#СВО-" in m["text"] for m in fake_max.messages)
        # экран 5: подписка на уведомления
        await bot.handle_update(_callback({"a": "sub", "v": 1}))
        assert store.get_user("u1")["subscribed"] == 1

    asyncio.run(scenario())


def test_topic_stub_for_gkh_and_roads():
    """Кнопки «Вопросы ЖКХ» и «Дороги» — заглушка «раздел в разработке»."""
    store = _temp_store()
    fake_max = FakeMax()
    bot = _bot(store, fake_max, FakeDocAI({"filled_fields": {}, "missing_fields": []}))

    async def scenario():
        await bot.handle_update(_callback({"a": "topic", "t": "gkh"}))
        await bot.handle_update(_callback({"a": "topic", "t": "roads"}))
        stubs = [m for m in fake_max.messages if "в разработке" in m["text"]]
        assert len(stubs) == 2
        # гид-опрос при этом не запускается
        assert store.get_bot_flow("u1") is None

    asyncio.run(scenario())


def _bot_started(chat_id: str = "100", user_id: str = "u1", name: str = "Иван") -> dict:
    """Апдейт первого контакта: гражданин нажал «Начать общение» в диалоге с ботом.

    Структура плоская (chat_id/user прямо в апдейте), а не как у message_created.
    """
    return {
        "update_type": "bot_started",
        "chat_id": chat_id,
        "user": {"user_id": user_id, "name": name},
    }


def test_bot_started_sends_welcome():
    """Кнопка «Начать общение» (первый контакт) — приветствие с кнопками тем."""
    from app.max.bot_logic import (
        MSG_TOPIC_GKH,
        MSG_TOPIC_MEASURES,
        MSG_TOPIC_ROADS,
        MSG_WELCOME,
    )

    store = _temp_store()
    fake_max = FakeMax()
    bot = _bot(store, fake_max, FakeDocAI({"filled_fields": {}, "missing_fields": []}))

    async def scenario():
        await bot.handle_update(_bot_started())

        assert len(fake_max.messages) == 1
        msg = fake_max.messages[0]
        assert msg["text"] == MSG_WELCOME
        assert msg["chat_id"] == "100"
        assert [row[0]["text"] for row in msg["rows"]] == [
            MSG_TOPIC_MEASURES, MSG_TOPIC_GKH, MSG_TOPIC_ROADS,
        ]
        # пользователь заведён — дальше идёт обычный сценарий по кнопкам
        assert store.get_user("u1") is not None

    asyncio.run(scenario())


def test_bot_started_resets_profile():
    """«Начать общение» = «начать сначала»: анкета и незакрытый опрос сбрасываются."""
    store = _temp_store()
    fake_max = FakeMax()
    bot = _bot(store, fake_max, FakeDocAI({"filled_fields": {}, "missing_fields": []}))

    async def scenario():
        store.ensure_user("u1", "100", "Иван", "")
        store.set_user_profile("u1", relation="Супруга", region_ok=True, locality="Видное")
        store.set_bot_flow("u1", "await_phone")

        await bot.handle_update(_bot_started())

        assert store.get_bot_flow("u1") is None
        row = store.get_user("u1")
        assert not row["relation"] and not row["locality"]

    asyncio.run(scenario())


def test_bot_started_without_chat_id_falls_back_to_user_id():
    """Если в апдейте нет chat_id — шлём в личный диалог по user_id."""
    store = _temp_store()
    fake_max = FakeMax()
    bot = _bot(store, fake_max, FakeDocAI({"filled_fields": {}, "missing_fields": []}))

    async def scenario():
        update = _bot_started()
        update.pop("chat_id")
        await bot.handle_update(update)

        assert len(fake_max.messages) == 1
        assert fake_max.messages[0]["chat_id"] == "u1"

    asyncio.run(scenario())


def test_webservice_measure_crud_and_template(tmp_path):
    from app.max.client import MaxClient
    from app.max.dify_client import DifyClient
    from app.web.service import WebService
    from app.web.usvo import UsvoStore

    cfg = Config()
    cfg.dify.measure_templates_dir = str(tmp_path / "tpl")
    store = Store(str(tmp_path / "state.db"))
    usvo = UsvoStore(cfg.web.usvo_xlsx, cfg.web.contact_stale_days)
    dify = DifyClient(cfg.dify.base_url, "", "", "")
    svc = WebService(cfg, store, usvo, dify, MaxClient(cfg.max.api_base_url, ""), None)

    res = svc.create_support_measure({
        "title": "Единовременная выплата",
        "description": "при ранении",
        "documents": ["Паспорт", "Справка из военкомата"],
        "placeholders": [{"key": "full_name", "label": "ФИО"}],
        "active": True,
    })
    assert res["ok"] is True
    mid = res["measure"]["id"]
    assert len(svc.list_support_measures()) == 1
    assert res["measure"]["documents"][1]["title"] == "Справка из военкомата"

    # загрузка шаблона распознаёт плейсхолдеры и привязывает файл
    out = svc.save_measure_template(mid, "tpl.docx", _make_template())
    assert out["ok"] is True
    keys = {p["key"] for p in out["placeholders"]}
    assert {"full_name", "passport_series", "phone_number"} <= keys
    m = svc.get_support_measure(mid)
    assert m["has_template"] is True and os.path.exists(m["template_path"])
    tpl_path = m["template_path"]

    # обновление и удаление (файл шаблона тоже удаляется)
    svc.update_support_measure(mid, {"title": "Выплата (ред.)", "active": False})
    assert svc.get_support_measure(mid)["title"] == "Выплата (ред.)"
    assert svc.delete_support_measure(mid)["ok"] is True
    assert svc.get_support_measure(mid) is None
    assert not os.path.exists(tpl_path)

    # KB не настроена → синхронизация мягко возвращает ok=False
    assert asyncio.run(svc.sync_measures_kb())["ok"] is False


class FakeDify:
    """Имитация базы знаний Dify: документы по имени, без сети."""
    def __init__(self):
        self.docs: dict[str, str] = {}

    def kb_ready(self):
        return True

    async def upsert_document_text(self, name, text, match_prefix=None):
        if match_prefix:
            for k in [k for k in self.docs if k.startswith(match_prefix) and k != name]:
                del self.docs[k]
        self.docs[name] = text
        return {"id": "doc"}

    async def delete_documents_by_prefix(self, prefix):
        ks = [k for k in self.docs if k.startswith(prefix)]
        for k in ks:
            del self.docs[k]
        return len(ks)

    async def list_all_documents(self, keyword=None, limit=100):
        # В этом фейке имя документа служит и его идентификатором.
        return [{"id": name, "name": name} for name in list(self.docs)
                if not keyword or keyword in name]

    async def delete_document(self, doc_id):
        self.docs.pop(doc_id, None)
        return {"result": "success"}


def _webservice(cfg, store, dify):
    from app.max.client import MaxClient
    from app.web.usvo import UsvoStore
    usvo = UsvoStore(cfg.web.usvo_xlsx, cfg.web.contact_stale_days)
    from app.web.service import WebService
    return WebService(cfg, store, usvo, dify, MaxClient(cfg.max.api_base_url, ""), None)


def test_kb_autosync_idempotent(tmp_path):
    cfg = Config()
    cfg.dify.measure_templates_dir = str(tmp_path / "tpl")
    store = Store(str(tmp_path / "state.db"))
    dify = FakeDify()
    svc = _webservice(cfg, store, dify)

    mid = svc.create_support_measure({"title": "Выплата", "documents": ["Паспорт"]})["measure"]["id"]
    asyncio.run(svc.sync_measure_to_kb(mid))
    assert len(dify.docs) == 1
    assert any(k.startswith(f"Мера поддержки #{mid}:") for k in dify.docs)

    # переименование меры не плодит дубль — документ обновляется на месте
    svc.update_support_measure(mid, {"title": "Выплата (ред.)"})
    asyncio.run(svc.sync_measure_to_kb(mid))
    assert len(dify.docs) == 1
    assert any("(ред.)" in k for k in dify.docs)

    # деактивация убирает меру из базы знаний
    svc.update_support_measure(mid, {"title": "Выплата (ред.)", "active": False})
    asyncio.run(svc.sync_measure_to_kb(mid))
    assert len(dify.docs) == 0


def test_sync_measures_kb_purges_orphans(tmp_path):
    """Полная пересинхронизация удаляет документы-призраки мер, которых нет в БД.

    Регрессия: раньше sync_measures_kb перебирал только меры из БД и не трогал
    осиротевшие документы «Мера поддержки #N» (после удаления меры или смены id БД),
    из-за чего ассистент подбора возвращал несуществующий measure_id."""
    cfg = Config()
    cfg.dify.measure_templates_dir = str(tmp_path / "tpl")
    store = Store(str(tmp_path / "state.db"))
    dify = FakeDify()
    svc = _webservice(cfg, store, dify)

    mid = svc.create_support_measure({"title": "Питание", "documents": ["Паспорт"]})["measure"]["id"]

    # Документы-призраки в базе знаний: мер с такими id в БД нет.
    dify.docs["Мера поддержки #404: Удалённая мера"] = "старое"
    dify.docs["Мера поддержки #405: Ещё одна удалённая"] = "старое"
    # Посторонний документ (не мера) не должен пострадать.
    dify.docs["Ответ оператора 2026-01-01 10:00"] = "ответ"

    res = asyncio.run(svc.sync_measures_kb())
    assert res["ok"] is True
    assert res["removed_orphans"] == 2

    names = set(dify.docs)
    assert any(k.startswith(f"Мера поддержки #{mid}:") for k in names)  # активная мера на месте
    assert not any("#404" in k or "#405" in k for k in names)          # призраки удалены
    assert "Ответ оператора 2026-01-01 10:00" in names                 # чужой документ цел


def test_measure_deactivated_midflow():
    store = _temp_store()
    mid = store.add_support_measure({
        "title": "Мера",
        "data": json.dumps({"documents": [{"title": "Паспорт", "sort_order": 0}],
                            "placeholders": [{"key": "full_name", "label": "ФИО"}]}),
        "active": True,
    })
    fake_max = FakeMax()
    bot = _bot(store, fake_max, FakeDocAI({"filled_fields": {}, "missing_fields": []}))

    async def scenario():
        await bot._start_measure_flow("u1", "100", "Иван", "", mid)
        row = store.get_active_flow("u1")
        store.set_support_measure_active(mid, False)  # админ выключил меру
        await bot._handle_message(_photo_msg())
        assert store.get_application(int(row["id"]))["status"] == "failed"
        assert any("недоступна" in m["text"].lower() for m in fake_max.messages)

    asyncio.run(scenario())
