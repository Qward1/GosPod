"""Чтение часто меняемого файла расписания (здания + слоты)."""
from __future__ import annotations

from dataclasses import dataclass

import yaml


@dataclass
class Slot:
    building_id: str
    building_name: str
    building_short: str
    address: str
    time: str


def load_schedule(path: str) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    if "buildings" not in data or not isinstance(data["buildings"], list):
        data["buildings"] = []
    return data


def iter_slots(path: str) -> list[Slot]:
    """Плоский список всех слотов всех зданий (для inline-кнопок выбора времени)."""
    data = load_schedule(path)
    slots: list[Slot] = []
    for b in data["buildings"]:
        bid = str(b.get("id", ""))
        name = str(b.get("name", ""))
        short = str(b.get("short") or name)
        address = str(b.get("address", ""))
        for t in b.get("slots", []) or []:
            slots.append(Slot(bid, name, short, address, str(t)))
    return slots


def find_building(path: str, building_id: str) -> dict | None:
    for b in load_schedule(path)["buildings"]:
        if str(b.get("id", "")) == str(building_id):
            return b
    return None
