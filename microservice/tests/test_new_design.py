"""Проверки доработок, приехавших вместе с новым дизайном кабинета.

Покрывают то, что нельзя увидеть в браузере «на глаз»:
  • профиль пользователя (ФИО по частям) и его переживание перелогина;
  • каноничные статусы УСВО и их синонимы;
  • окно графика аналитики (день/неделя/месяц + почасовой разрез);
  • закрепление диалогов «ИИ ассистента»;
  • SPA-маршруты фронтенда, которые обязан отдавать сервер.

Сети и Dify здесь нет — только stdlib, SQLite во временном каталоге и
fastapi.testclient.
"""
from __future__ import annotations

import datetime as dt
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.auth.employees import EmployeeStore, compose_name, split_name  # noqa: E402
from app.max.store import Store  # noqa: E402
from app.web.analytics import _build_series, _distribute_hourly  # noqa: E402
from app.web.usvo import (  # noqa: E402
    STATUSES,
    canonical_status,
    default_status,
    record_from_fields,
)


# ---- профиль пользователя кабинета -----------------------------------------

@pytest.fixture()
def employees(tmp_path) -> EmployeeStore:
    return EmployeeStore(str(tmp_path / "employees.db"))


def test_split_and_compose_name_roundtrip():
    assert split_name("Иванов Иван Иванович") == ("Иванов", "Иван", "Иванович")
    assert split_name("Иванов Иван") == ("Иванов", "Иван", "")
    assert split_name("Оператор") == ("", "Оператор", "")
    assert split_name("   ") == ("", "", "")
    # Составное отчество не теряется.
    assert split_name("Петров Пётр Ибн Хаттаб")[2] == "Ибн Хаттаб"
    assert compose_name("Иванов", "", "Иванович") == "Иванов Иванович"


def test_profile_for_admin_without_employee_row(employees: EmployeeStore):
    """Админ из demo_users своей строки в employees не имеет — профиль в user_profiles."""
    profile = employees.get_profile_by_sub("admin@mosreg.ru", fallback_name="Петров С. А.")
    assert (profile["last_name"], profile["first_name"]) == ("Петров", "С.")

    saved = employees.update_profile_by_sub(
        "admin@mosreg.ru",
        last_name="Петров", first_name="Сергей", middle_name="Алексеевич",
        birth_date="1981-04-12", phone="+7 916 000-11-22",
    )
    assert saved["name"] == "Петров Сергей Алексеевич"

    again = employees.get_profile_by_sub("admin@mosreg.ru", fallback_name="Петров С. А.")
    assert again["name"] == "Петров Сергей Алексеевич"
    assert again["birth_date"] == "1981-04-12"
    assert again["phone"] == "+7 916 000-11-22"


def test_profile_for_employee_updates_employee_row(employees: EmployeeStore):
    """У сотрудника профиль — это его строка employees, ФИО видно в списке ответственных."""
    emp = employees.create("Сидорова М. В.", "sidorova", "secret123", position="Оператор")
    sub = f"emp:{emp['id']}"

    profile = employees.get_profile_by_sub(sub)
    assert profile["login"] == "sidorova"
    assert profile["last_name"] == "Сидорова"

    employees.update_profile_by_sub(
        sub, last_name="Сидорова", first_name="Мария", middle_name="Викторовна",
    )
    assert employees.active_names() == ["Сидорова Мария Викторовна"]
    assert employees.get(emp["id"])["name"] == "Сидорова Мария Викторовна"


def test_profile_requires_a_name(employees: EmployeeStore):
    with pytest.raises(ValueError):
        employees.update_profile_by_sub("admin@mosreg.ru", last_name=" ", first_name="")


def test_profile_phone_none_keeps_previous_value(employees: EmployeeStore):
    employees.update_profile_by_sub("a@b", last_name="Иванов", first_name="Иван",
                                    phone="+7 900 000-00-00")
    kept = employees.update_profile_by_sub("a@b", last_name="Иванов", first_name="Иван",
                                           phone=None)
    assert kept["phone"] == "+7 900 000-00-00"
    cleared = employees.update_profile_by_sub("a@b", last_name="Иванов", first_name="Иван",
                                              phone="")
    assert cleared["phone"] == ""


def test_existing_employees_get_fio_backfilled(tmp_path):
    """Повторное открытие старой БД разбирает name на ФИО-части (авто-миграция)."""
    path = str(tmp_path / "employees.db")
    first = EmployeeStore(path)
    emp = first.create("Кузнецова Нина Николаевна", "kuz", "pwd12345")
    # Эмулируем «старую» запись без ФИО-частей.
    with first._conn() as c:  # noqa: SLF001 — проверяем именно миграцию хранилища
        c.execute("UPDATE employees SET last_name='', first_name='', middle_name='' WHERE id=?",
                  (emp["id"],))

    reopened = EmployeeStore(path)
    row = reopened.get(emp["id"])
    assert (row["last_name"], row["first_name"], row["middle_name"]) == (
        "Кузнецова", "Нина", "Николаевна",
    )


# ---- статусы УСВО ----------------------------------------------------------

def test_canonical_status_maps_synonyms():
    assert canonical_status("контракт") == "Контрактник"
    assert canonical_status("По контракту") == "Контрактник"
    assert canonical_status("ВБД") == "Ветеран боевых действий"
    assert canonical_status("мобилизованный") == "Мобилизованный"


def test_canonical_status_keeps_unknown_values():
    assert canonical_status("Доброволец БАРС") == "Доброволец БАРС"


def test_canonical_status_fills_empty_from_sequence():
    assert canonical_status("", 3) == default_status(3)
    assert canonical_status("—", 4) in STATUSES
    # Без порядкового номера пустое остаётся пустым.
    assert canonical_status("") == ""


def test_record_from_fields_never_leaves_status_dash():
    rec = record_from_fields(7, [("ФИО", "Тестов Тест Тестович"), ("Телефон", "+7 900 000")])
    assert rec.status in STATUSES


# ---- аналитика: окно графика и почасовой разрез -----------------------------

def test_build_series_respects_window():
    end = dt.date(2026, 5, 31)
    series = _build_series([], days=31, end=end)
    day = next(s for s in series if s["key"] == "day")
    assert len(day["points"]) == 31
    assert day["points"][0]["date"] == "01.05"
    assert day["points"][-1]["date"] == "31.05"
    assert len(day["hourly"]) == 24
    assert day["unit_hourly"] == "обр./час"


def test_build_series_single_day_window():
    end = dt.date(2026, 7, 29)
    day = next(s for s in _build_series([], days=1, end=end) if s["key"] == "day")
    assert [p["date"] for p in day["points"]] == ["29.07"]
    # Скользящие окна 7/30 дней не должны падать при days=1.
    week = next(s for s in _build_series([], days=1, end=end) if s["key"] == "week")
    assert week["points"][0]["count"] > 0


def test_build_series_is_deterministic():
    end = dt.date(2026, 3, 15)
    assert _build_series([], days=7, end=end) == _build_series([], days=7, end=end)


def test_distribute_hourly_labels_and_shape():
    hourly = _distribute_hourly(48, seed=401, appeal_times=None, day=dt.date(2026, 7, 29))
    assert len(hourly) == 24
    assert hourly[0]["date"] == "00:00"
    assert hourly[-1]["date"] == "23:00"
    assert all(p["count"] >= 0 for p in hourly)
    # Днём нагрузка выше, чем ночью.
    assert sum(p["count"] for p in hourly[9:18]) > sum(p["count"] for p in hourly[0:5])


# ---- закреплённые диалоги ИИ-ассистента -------------------------------------

@pytest.fixture()
def store(tmp_path) -> Store:
    return Store(str(tmp_path / "state.db"))


def test_pinned_chat_goes_first(store: Store):
    old = store.create_ai_chat("u1", "Старый")
    new = store.create_ai_chat("u1", "Новый")
    assert [r["id"] for r in store.list_ai_chats("u1")] == [new, old]

    store.update_ai_chat(old, "u1", pinned=True)
    assert [r["id"] for r in store.list_ai_chats("u1")] == [old, new]

    store.update_ai_chat(old, "u1", pinned=False)
    assert [r["id"] for r in store.list_ai_chats("u1")] == [new, old]


def test_pinning_does_not_bump_updated_at(store: Store):
    chat_id = store.create_ai_chat("u1", "Чат")
    before = store.get_ai_chat(chat_id, "u1")["updated_at"]
    store.update_ai_chat(chat_id, "u1", pinned=True)
    assert store.get_ai_chat(chat_id, "u1")["updated_at"] == before
    # А переименование — двигает.
    store.update_ai_chat(chat_id, "u1", title="Другое имя")
    assert store.get_ai_chat(chat_id, "u1")["updated_at"] > before


def test_pinned_flag_is_isolated_per_user(store: Store):
    mine = store.create_ai_chat("u1", "Мой")
    assert store.update_ai_chat(mine, "u2", pinned=True) is False
    assert bool(store.get_ai_chat(mine, "u1")["pinned"]) is False


# ---- SPA-маршруты ----------------------------------------------------------

def test_spa_routes_match_frontend_router():
    """Список маршрутов в main.py обязан покрывать <Route> из App.tsx.

    Разъезд этих списков — это 404 на перезагрузке страницы, который в тестах
    API не виден, поэтому сверяем их напрямую по исходникам.
    """
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    with open(os.path.join(root, "app", "main.py"), encoding="utf-8") as f:
        main_src = f.read()
    app_tsx = os.path.join(root, "frontend", "src", "App.tsx")
    if not os.path.exists(app_tsx):  # исходники фронтенда могут не поставляться
        pytest.skip("frontend/src/App.tsx отсутствует")
    with open(app_tsx, encoding="utf-8") as f:
        tsx_src = f.read()

    import re
    # Путь карточки собирается f-строкой, поэтому в исходнике фигурные скобки
    # удвоены — разворачиваем, чтобы сравнивать с записью маршрута как есть.
    main_src = main_src.replace("{{", "{").replace("}}", "}")

    # В App.tsx часть путей записана с ведущим слэшем, часть — без (вложенные
    # маршруты относительно ProtectedShell). Нормализуем к одному виду.
    declared = {"/" + r.lstrip("/") for r in re.findall(r'<Route path="([^"]+)"', tsx_src)}
    declared.discard("/*")
    assert declared, "не нашли ни одного <Route> — изменился формат App.tsx"
    for route in declared:
        if route.startswith("/usvo/cards"):
            assert "/usvo/cards/{rec_id}" in main_src
            continue
        assert f'"{route}"' in main_src, f"маршрут {route} не отдаётся сервером"
