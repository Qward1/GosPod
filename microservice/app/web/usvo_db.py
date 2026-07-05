"""Загрузчик персональных карточек УСВО, сохранённых в БД (таблица usvo_cards).

Карточки попадают сюда после загрузки Excel-файла из веб-кабинета
(см. app/web/import_usvo.py). В отличие от табличного UsvoStore (читает Excel из
задания 1), здесь источник — SQLite, и у каждой карточки есть нормализованная
«История взаимодействия» (события с иконкой/статусом/стилем), полученная из Dify
или офлайн-нормализатора (см. app/web/history_ai.py).

Чтобы id загруженных карточек не пересекались с табличными (1..N), к id строки
прибавляется большое смещение USVO_DB_ID_BASE.
"""
from __future__ import annotations

import json
import threading

from app.max.store import Store
from app.web.usvo import UsvoRecord, record_from_fields

# Загруженные карточки получают id вида 100000 + rowid — заведомо вне диапазона
# табличных карточек (их id равны порядковому номеру строки Excel).
USVO_DB_ID_BASE = 100_000


def is_db_id(rec_id: int) -> bool:
    return rec_id >= USVO_DB_ID_BASE


def row_id_of(rec_id: int) -> int:
    return rec_id - USVO_DB_ID_BASE


class UsvoCardStore:
    """Кэширующий загрузчик карточек УСВО из БД (перечитывает при изменении набора)."""

    def __init__(self, store: Store, stale_days: int = 90):
        self.store = store
        self.stale_days = stale_days
        self._lock = threading.Lock()
        self._signature: tuple | None = None
        self._records: list[UsvoRecord] = []

    def _row_to_record(self, row) -> UsvoRecord:
        d = dict(row)
        try:
            data = json.loads(d.get("data") or "{}")
        except Exception:  # noqa: BLE001
            data = {}
        fields = [
            (f.get("label", ""), f.get("value", ""))
            for f in (data.get("fields") or [])
            if isinstance(f, dict)
        ]
        rec = record_from_fields(
            USVO_DB_ID_BASE + int(d["id"]),
            fields,
            history=data.get("history"),
            history_raw=data.get("history_raw", ""),
            head_directive=data.get("head_directive"),
            stale_days=self.stale_days,
        )
        # Ключевые значения из колонок таблицы перекрывают вывод из полей (на случай,
        # если оператор заполнил шапку отдельно).
        rec.name = d.get("name") or rec.name
        rec.short_name = rec.short_name
        rec.status = d.get("status") or rec.status
        rec.phone = d.get("phone") or rec.phone
        rec.address = d.get("address") or rec.address
        rec.birth_date = d.get("birth_date") or rec.birth_date
        rec.call_date = d.get("call_date") or rec.call_date
        rec.awards = d.get("awards") or rec.awards
        return rec

    def _signature_now(self) -> tuple:
        rows = self.store.list_usvo_cards()
        # Учитываем updated_at — иначе правка «на месте» (число строк и created_at
        # неизменны) не сбросит кэш и редактирование не отобразится.
        last = max((self._row_ts(r) for r in rows), default=0)
        return (len(rows), last)

    @staticmethod
    def _row_ts(row) -> float:
        d = dict(row)
        return float(d.get("updated_at") or d.get("created_at") or 0)

    def _maybe_reload(self) -> None:
        with self._lock:
            try:
                sig = self._signature_now()
            except Exception:  # noqa: BLE001
                sig = (0, 0)
            if sig != self._signature:
                self._signature = sig
                rows = self.store.list_usvo_cards()
                self._records = [self._row_to_record(r) for r in rows]

    def all(self) -> list[UsvoRecord]:
        self._maybe_reload()
        # Новые карточки — сверху (id по убыванию).
        return list(reversed(self._records))

    def get(self, rec_id: int) -> UsvoRecord | None:
        if not is_db_id(rec_id):
            return None
        for r in self.all():
            if r.id == rec_id:
                return r
        return None

    def count(self) -> int:
        return self.store.count_usvo_cards()
