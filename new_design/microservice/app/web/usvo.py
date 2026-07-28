"""Чтение таблицы УСВО в структурированные персональные карточки."""
from __future__ import annotations

import datetime as dt
import hashlib
import os
import re
import threading
from dataclasses import dataclass, field

import openpyxl
from openpyxl.utils.datetime import from_excel

# индексы столбцов (1-based) по макету таблицы; недостающие просто пропускаются
PRIMARY_INDICES = [1, 4, 5, 6, 7, 8, 9, 10, 11, 12, 18, 19, 20, 21, 22, 23, 24, 25, 26, 27]
SECONDARY_INDICES = [13, 14, 15, 16, 17, 28, 29, 30, 31, 32, 33, 34]

# Ключевые столбцы для шапки карточки и аналитики ищем по подстроке в заголовке
# (устойчиво к перестановке столбцов и хвостовым пробелам).
KEY_HEADERS = {
    "name": ["фио  усво", "фио усво", "ф.и.о. усво", "фио участник"],
    "birth": ["дата рождения"],
    "call_date": ["дата обзвона"],
    "phone": ["телефон"],
    "address": ["адрес регистрации"],
    "status": ["статус"],
    "employment_need": ["нуждается в трудоустройстве"],
    "support_need": ["в каких мерах поддержки"],
    "org_vremya": ["время героя"],
    "org_geroi_mo": ["герои подмосковья"],
    "org_assoc": ["ассоциация"],
    "vbd": ['ветеран боевых действий'],
    "awards": ["награды"],
}


@dataclass
class Field:
    label: str
    value: str


@dataclass
class UsvoRecord:
    id: int
    name: str
    short_name: str
    status: str
    call_date: str
    birth_date: str
    phone: str
    address: str
    awards: str
    initials: str
    primary: list[Field] = field(default_factory=list)
    secondary: list[Field] = field(default_factory=list)
    extra: list[Field] = field(default_factory=list)
    flags: dict = field(default_factory=dict)  # для аналитики
    head_directive: dict | None = None  # поручение/личная встреча с Главой округа
    source: str = "table"  # table (Excel) | uploaded (загружено из кабинета)
    # Для загруженных карточек — нормализованная «История взаимодействия»
    # (события с иконкой/статусом/стилем). У табличных — None (достраивается синтетикой).
    history: list[dict] | None = None
    history_raw: str = ""

    def as_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "short_name": self.short_name,
            "initials": self.initials,
            "status": self.status,
            "call_date": self.call_date,
            "birth_date": self.birth_date,
            "phone": self.phone,
            "address": self.address,
            "awards": self.awards,
            "primary": [f.__dict__ for f in self.primary],
            "secondary": [f.__dict__ for f in self.secondary],
            "extra": [f.__dict__ for f in self.extra],
            "flags": self.flags,
            "head_directive": self.head_directive,
            "source": self.source,
        }


# поручения Главы округа назначаются части карточек детерминированно по id
_HEAD_MEETING_TOPICS = [
    "о выделении земельного участка для индивидуального жилищного строительства",
    "о содействии в газификации частного дома",
    "о внеочередном предоставлении места в детском саду для ребёнка",
    "о ремонте кровли многоквартирного дома по месту жительства",
    "о трудоустройстве на муниципальное предприятие после реабилитации",
    "об оказании адресной материальной помощи семье",
]
_HEAD_PROGRAMS = [
    "по обеспечению многодетных семей участников СВО льготными кружками в детских "
    "садах и школах",
    "по первоочередному зачислению детей участников СВО в группы продлённого дня",
    "по бесплатному санаторно-курортному оздоровлению ветеранов боевых действий "
    "Ленинского округа",
    "по освобождению семей участников СВО от платы за школьное питание",
    "по предоставлению бесплатных секций и кружков детям мобилизованных граждан",
]


def _head_directive(seq: int, call_date: str) -> dict | None:
    """Детерминированно решает, есть ли над карточкой строка о поручении Главы."""
    if seq % 3 == 1:  # личная встреча
        topic = _HEAD_MEETING_TOPICS[(seq // 3) % len(_HEAD_MEETING_TOPICS)]
        meet_date = call_date or (dt.date(2026, 2, 5) + dt.timedelta(days=seq)).strftime("%d.%m.%Y")
        return {
            "type": "meeting",
            "text": f"Лично встречался с Главой округа {meet_date} для решения вопроса {topic}.",
        }
    if seq % 3 == 2:  # тематическое поручение
        program = _HEAD_PROGRAMS[(seq // 3) % len(_HEAD_PROGRAMS)]
        return {
            "type": "program",
            "text": f"Попадает под поручение Главы округа {program}.",
        }
    return None  # у трети карточек строки нет


def _fmt(value, number_format: str | None = None) -> str:
    if value is None:
        return ""
    if isinstance(value, (dt.datetime, dt.date)):
        return value.strftime("%d.%m.%Y")
    if isinstance(value, (int, float)) and number_format and "y" in str(number_format).lower():
        try:
            conv = from_excel(value)
            if isinstance(conv, (dt.datetime, dt.date)):
                return conv.strftime("%d.%m.%Y")
        except Exception:
            pass
    text = str(value).strip()
    return " ".join(p.strip() for p in text.splitlines() if p.strip())


def _short_name(full: str) -> str:
    """«Иванов Иван Иванович» → «Иванов И. И.»"""
    parts = full.split()
    if len(parts) >= 3:
        return f"{parts[0]} {parts[1][:1]}. {parts[2][:1]}."
    if len(parts) == 2:
        return f"{parts[0]} {parts[1][:1]}."
    return full


def _initials(full: str) -> str:
    parts = [p for p in full.split() if p]
    if len(parts) >= 2:
        return (parts[0][:1] + parts[1][:1]).upper()
    if parts:
        return parts[0][:2].upper()
    return "—"


def _maybe_excel_date(value: str) -> str:
    """Excel-серийный номер даты («27960») → «12.06.1976»."""
    s = (value or "").strip()
    if not s.isdigit():
        return value
    n = int(s)
    if 10000 <= n <= 51000:
        try:
            conv = from_excel(n)
            if isinstance(conv, (dt.datetime, dt.date)):
                return conv.strftime("%d.%m.%Y")
        except Exception:
            pass
    return value


def _parse_call_date(value: str) -> dt.date | None:
    if not value:
        return None
    for fmt in ("%d.%m.%Y", "%Y-%m-%d", "%d.%m.%y"):
        try:
            return dt.datetime.strptime(value.strip(), fmt).date()
        except ValueError:
            continue
    return None


LAST_NAMES = [
    "Андреев", "Белов", "Васильев", "Громов", "Демидов", "Егоров", "Захаров",
    "Ильин", "Киселев", "Лебедев", "Морозов", "Никитин", "Орлов", "Павлов",
    "Романов", "Смирнов", "Титов", "Федоров", "Чернов", "Шестаков",
]
FIRST_NAMES = [
    "Александр", "Алексей", "Андрей", "Владимир", "Дмитрий", "Иван", "Константин",
    "Максим", "Михаил", "Николай", "Олег", "Павел", "Роман", "Сергей", "Юрий",
]
PATRONYMICS = [
    "Александрович", "Андреевич", "Викторович", "Владимирович", "Дмитриевич",
    "Иванович", "Михайлович", "Николаевич", "Петрович", "Сергеевич",
]
STATUSES = ["Контрактник", "Мобилизованный", "Доброволец", "Ветеран боевых действий"]


def default_status(seq: int) -> str:
    return STATUSES[(seq - 1) % len(STATUSES)]
FAMILY = ["женат", "не женат", "разведен"]
EDUCATION = ["среднее профессиональное", "высшее", "среднее общее"]
PROFESSIONS = ["водитель", "электрик", "слесарь", "оператор ПК", "охранник", "механик"]
SUPPORT_NEEDS = [
    "помощь в трудоустройстве",
    "оформление выплат и льгот",
    "медицинская реабилитация",
    "юридическая консультация",
    "психологическая помощь",
]
ADMIN_STAFF = ["Иванова О. П.", "Петров С. А.", "Сидорова М. В.", "Кузнецова Н. Н."]
# Реалистичные адреса — населённые пункты и улицы Ленинского городского округа МО
# (согласованы с очагами тепловой карты в analytics.py).
LOCALITIES = [
    ("г. Видное", ["Заводская", "Школьная", "Берёзовая", "Советская",
                   "Гаевского", "Радужная", "Олимпийская", "Битцевский проезд"]),
    ("рп. Горки Ленинские", ["Центральная", "Зелёная", "Молодёжная", "Полевая"]),
    ("пос. Развилка", ["Развилка", "Дачная", "Лесная"]),
    ("дер. Дрожжино", ["Новое Шоссе", "Воскресенская", "Южная"]),
    ("пос. совхоза им. Ленина", ["Главная", "Садовая", "Восточная"]),
    ("пос. Володарского", ["Текстильщиков", "Зелёная", "Первомайская"]),
    ("дер. Молоково", ["Школьная", "Озёрная", "Луговая"]),
    ("дер. Мисайлово", ["Молодёжный бульвар", "Литературный бульвар", "Пожарная"]),
]
# Реалистичные женские имёна для родственников (жён) и сотрудниц.
FEMALE_FIRST = ["Анна", "Елена", "Ирина", "Марина", "Наталья", "Ольга", "Светлана", "Татьяна"]
FEMALE_PATRONYMICS = [
    "Александровна", "Андреевна", "Викторовна", "Владимировна", "Дмитриевна",
    "Ивановна", "Михайловна", "Николаевна", "Петровна", "Сергеевна",
]
# Реалистичные награды — чтобы медали отображались на демо-данных.
AWARDS = [
    "Орден Мужества",
    "Медаль «За отвагу»",
    "Медаль «За боевые отличия»",
    "Медаль Суворова",
    "Орден Мужества, Медаль «За отвагу»",
    "Медаль Жукова",
    "Георгиевский крест IV степени",
    "Медаль «За спасение погибавших»",
    "Орден «За заслуги перед Отечеством» II степени",
    "Медаль «За воинскую доблесть»",
    "Медаль «За боевые отличия», Благодарность командования",
]


def _synthetic_name(seq: int) -> str:
    return (
        f"{LAST_NAMES[(seq - 1) % len(LAST_NAMES)]} "
        f"{FIRST_NAMES[(seq * 2 - 1) % len(FIRST_NAMES)]} "
        f"{PATRONYMICS[(seq * 3 - 1) % len(PATRONYMICS)]}"
    )


def _synthetic_value(header: str, seq: int) -> str:
    h = header.lower()
    if "фио  усво" in h or "фио усво" in h:
        return _synthetic_name(seq)
    if "дата обзвона" in h:
        return (dt.date(2026, 1, 10) + dt.timedelta(days=seq * 3)).strftime("%d.%m.%Y")
    if "дата рождения" in h:
        # правдоподобная дата рождения (1972–2003), детерминирована по seq
        year = 1972 + (seq * 7 + 3) % 32
        month = 1 + (seq * 5) % 12
        day = 1 + (seq * 11) % 28
        return f"{day:02d}.{month:02d}.{year}"
    if h == "статус" or h.startswith("статус "):
        return default_status(seq)
    if "мобилизованн" in h or "доброволец" in h or "контракт" in h:
        return default_status(seq + 1)
    if "ик/" in h or "чвк" in h or "сизо" in h:
        return "нет"
    if "примечание к статусу" in h:
        return "нет" if seq % 4 else "инвалидность 3 группа"
    if "состоянию здоровья" in h:
        return "без тяжелых заболеваний" if seq % 5 else "требуется реабилитация"
    if "адрес регистрации" in h:
        locality, streets = LOCALITIES[(seq - 1) % len(LOCALITIES)]
        street = streets[(seq * 3) % len(streets)]
        house = 1 + (seq * 7) % 60
        flat = 1 + (seq * 13) % 180
        return f"Московская область, Ленинский г.о., {locality}, ул. {street}, д. {house}, кв. {flat}"
    if "телефон" in h or h == "контакты":
        # Реалистичный мобильный номер: +7 (9XX) XXX-XX-XX.
        codes = ["903", "905", "915", "916", "925", "926", "977", "999"]
        code = codes[(seq - 1) % len(codes)]
        a = 100 + (seq * 37) % 900
        b = (seq * 53) % 100
        c = (seq * 71) % 100
        return f"+7 ({code}) {a:03d}-{b:02d}-{c:02d}"
    if "семейное положение" in h:
        return FAMILY[(seq - 1) % len(FAMILY)]
    if "родственника" in h or "родственников" in h:
        # Жена: фамилия как у участника, женское имя и отчество.
        last = LAST_NAMES[(seq - 1) % len(LAST_NAMES)] + "а"
        first = FEMALE_FIRST[(seq * 2) % len(FEMALE_FIRST)]
        patr = FEMALE_PATRONYMICS[(seq * 3) % len(FEMALE_PATRONYMICS)]
        return f"{last} {first} {patr} (супруга)"
    if "дети" in h and "ограниченными" not in h:
        return "нет" if seq % 3 else f"сын, {2010 + seq % 14} г.р."
    if "ограниченными возможностями" in h:
        return "нет"
    if "ветеран боевых действий" in h:
        return "да" if seq % 2 else "оформляется"
    if "награды" in h:
        return "нет сведений" if seq % 5 == 0 else AWARDS[(seq - 1) % len(AWARDS)]
    if "образование" in h and "дополнительное" not in h:
        return EDUCATION[(seq - 1) % len(EDUCATION)]
    if "специальность" in h or "профессия" in h:
        return PROFESSIONS[(seq - 1) % len(PROFESSIONS)]
    if "дополнительное образование" in h:
        return "нужно" if seq % 3 == 0 else "не требуется"
    if "прежнее место работы" in h:
        return "нет" if seq % 4 else "да"
    if "трудоустройстве" in h:
        return "нуждается" if seq % 3 == 0 else "не нуждается"
    if "время героя" in h or "герои подмосковья" in h:
        return "нет" if seq % 4 else "участник"
    if "ассоциация" in h:
        return "состоит" if seq % 5 == 0 else "не состоит"
    if "ответственный от администрации" in h or "должностное лицо" in h:
        return ADMIN_STAFF[(seq - 1) % len(ADMIN_STAFF)]
    if "мерах поддержки" in h:
        return SUPPORT_NEEDS[(seq - 1) % len(SUPPORT_NEEDS)]
    if "тер." in h or "место работы" in h or "должность" in h:
        return "Территориальный отдел социальной поддержки"
    return "нет сведений"


class UsvoStore:
    """Кэширующий загрузчик таблицы УСВО (перечитывает по mtime)."""

    def __init__(self, path: str, stale_days: int = 90):
        self.path = path
        self.stale_days = stale_days
        self._lock = threading.Lock()
        self._mtime: float | None = None
        self._records: list[UsvoRecord] = []
        self._error: str | None = None

    def _key_index(self, headers: list[str], key: str) -> int | None:
        needles = KEY_HEADERS.get(key, [])
        for i, h in enumerate(headers):
            hl = h.lower()
            if any(n in hl for n in needles):
                return i + 1  # 1-based
        return None

    def _load(self) -> None:
        if not os.path.exists(self.path):
            self._records = []
            self._error = f"Файл таблицы УСВО не найден: {self.path}"
            return

        try:
            wb = openpyxl.load_workbook(self.path, data_only=True, read_only=True)
        except Exception as exc:  # noqa: BLE001
            self._records = []
            self._error = f"Не удалось открыть таблицу УСВО: {exc}"
            return

        ws = wb.worksheets[0]
        rows = ws.iter_rows()
        try:
            header_cells = next(rows)
        except StopIteration:
            wb.close()
            self._records = []
            self._error = "Таблица УСВО пуста"
            return

        headers = [" ".join(str(c.value or "").split()) or f"Колонка {i+1}"
                   for i, c in enumerate(header_cells)]
        idx = {k: self._key_index(headers, k) for k in KEY_HEADERS}

        def display_value(values: list[str], one_based: int | None):
            if not one_based or one_based - 1 >= len(values):
                return ""
            return values[one_based - 1]

        records: list[UsvoRecord] = []
        today = dt.date.today()
        seq = 0
        for row in rows:
            values = [_fmt(c.value, c.number_format) for c in row]
            seq += 1
            for i, header in enumerate(headers):
                if i >= len(values):
                    values.append("")
                if not values[i]:
                    values[i] = _synthetic_value(header, seq)
                elif "дата" in header.lower():
                    # Дата, сохранённая в xlsx числом (Excel-серийный номер), → ДД.ММ.ГГГГ.
                    values[i] = _maybe_excel_date(values[i])

            name = display_value(values, idx.get("name")) or _synthetic_name(seq)

            call_date = display_value(values, idx.get("call_date"))
            status = display_value(values, idx.get("status")) or default_status(seq)
            rec = UsvoRecord(
                id=seq,
                name=name,
                short_name=_short_name(name),
                initials=_initials(name),
                status=status,
                call_date=call_date,
                birth_date=display_value(values, idx.get("birth")),
                phone=display_value(values, idx.get("phone")),
                address=display_value(values, idx.get("address")),
                awards=display_value(values, idx.get("awards")),
            )

            used = set()
            for one_based in PRIMARY_INDICES:
                if one_based - 1 < len(headers):
                    v = values[one_based - 1] if one_based - 1 < len(values) else ""
                    if v:
                        rec.primary.append(Field(headers[one_based - 1], v))
                    used.add(one_based)
            for one_based in SECONDARY_INDICES:
                if one_based - 1 < len(headers):
                    v = values[one_based - 1] if one_based - 1 < len(values) else ""
                    if v:
                        rec.secondary.append(Field(headers[one_based - 1], v))
                    used.add(one_based)
            # столбцы вне макета — в «Дополнительно» (кроме ключевых шапки)
            header_only = {idx.get("name"), idx.get("call_date"), 2}
            for i, h in enumerate(headers, start=1):
                if i in used or i in header_only:
                    continue
                v = values[i - 1] if i - 1 < len(values) else ""
                if v:
                    rec.extra.append(Field(h, v))

            # Флаги для аналитики
            emp = display_value(values, idx.get("employment_need")).lower()
            cdate = _parse_call_date(call_date)
            rec.flags = {
                "unemployed": ("требуется" in emp or "нуждает" in emp or "не трудоустроен" in emp),
                "support_need": display_value(values, idx.get("support_need")),
                "org_vremya": _is_yes(display_value(values, idx.get("org_vremya"))),
                "org_geroi_mo": _is_yes(display_value(values, idx.get("org_geroi_mo"))),
                "org_assoc": _is_member(display_value(values, idx.get("org_assoc"))),
                "vbd": _is_yes(display_value(values, idx.get("vbd"))),
                "stale_contact": bool(cdate and (today - cdate).days > self.stale_days),
                "status": status,
            }
            rec.head_directive = _head_directive(seq, call_date)
            records.append(rec)

        wb.close()
        self._records = records
        self._error = None

    def _maybe_reload(self) -> None:
        with self._lock:
            try:
                mtime = os.path.getmtime(self.path) if os.path.exists(self.path) else None
            except OSError:
                mtime = None
            if mtime != self._mtime or (self._error and not self._records):
                self._mtime = mtime
                self._load()

    def all(self) -> list[UsvoRecord]:
        self._maybe_reload()
        return self._records

    def get(self, rec_id: int) -> UsvoRecord | None:
        for r in self.all():
            if r.id == rec_id:
                return r
        return None

    @property
    def error(self) -> str | None:
        self._maybe_reload()
        return self._error


def _is_yes(value: str) -> bool:
    v = (value or "").strip().lower()
    if not v:
        return False
    if v.startswith("нет") or v == "no" or v == "-":
        return False
    return v in ("да", "имеет", "есть", "участник", "yes", "является") or "участ" in v or "имеет" in v


def _is_member(value: str) -> bool:
    """Членство в ассоциации: учитываем фактическое участие, не «желает вступить»."""
    v = (value or "").strip().lower()
    if not v or "желает" in v or v.startswith("нет"):
        return False
    return True


# --- Построение карточки из произвольного набора полей (label → value). Используется загрузчиком карточек из веб-кабинета (usvo_db.py): таблица, которую загрузил оператор, может иметь любой набор столбцов, поэтому первичные/ вторичные данные и флаги для аналитики определяются по СМЫСЛУ заголовка, а не по фиксированной позиции столбца (как в каноничной таблице из задания 1). ---
def _label_matches_key(label: str, key: str) -> bool:
    ll = (label or "").lower()
    # у загруженных таблиц заголовок часто просто «Статус» — матчим подстрокой,
    # исключая «примечание к статусу»
    if key == "status":
        return "статус" in ll and "примечание" not in ll
    needles = KEY_HEADERS.get(key, [])
    return any(n in ll for n in needles)


def find_field_value(fields: list[tuple[str, str]], key: str) -> str:
    """Значение поля карточки по семантическому ключу (см. KEY_HEADERS)."""
    for label, value in fields:
        if _label_matches_key(label, key):
            return value
    return ""


def compute_flags(fields: list[tuple[str, str]], status: str,
                  call_date: str, stale_days: int = 90) -> dict:
    """Флаги для аналитики/фильтров из набора полей (label, value)."""
    emp = find_field_value(fields, "employment_need").lower()
    cdate = _parse_call_date(call_date)
    today = dt.date.today()
    return {
        "unemployed": ("требуется" in emp or "нуждает" in emp or "не трудоустроен" in emp),
        "support_need": find_field_value(fields, "support_need"),
        "org_vremya": _is_yes(find_field_value(fields, "org_vremya")),
        "org_geroi_mo": _is_yes(find_field_value(fields, "org_geroi_mo")),
        "org_assoc": _is_member(find_field_value(fields, "org_assoc")),
        "vbd": _is_yes(find_field_value(fields, "vbd")),
        "stale_contact": bool(cdate and (today - cdate).days > stale_days),
        "status": status,
    }


def record_from_fields(
    rec_id: int,
    fields: list[tuple[str, str]],
    *,
    history: list[dict] | None = None,
    history_raw: str = "",
    head_directive: dict | None = None,
    stale_days: int = 90,
) -> UsvoRecord:
    """Собирает UsvoRecord из произвольного списка (label, value)."""
    clean = [(l, v) for (l, v) in fields if (v or "").strip()]
    name = find_field_value(clean, "name") or _synthetic_name(rec_id)
    status = find_field_value(clean, "status") or default_status(rec_id)
    call_date = find_field_value(clean, "call_date")
    rec = UsvoRecord(
        id=rec_id,
        name=name,
        short_name=_short_name(name),
        initials=_initials(name),
        status=status,
        call_date=call_date,
        birth_date=_maybe_excel_date(find_field_value(clean, "birth")),
        phone=find_field_value(clean, "phone"),
        address=find_field_value(clean, "address"),
        awards=find_field_value(clean, "awards"),
        source="uploaded",
        history=history,
        history_raw=history_raw,
    )
    # Все непустые поля — в primary (награды показываются медалями, но пусть лежат).
    rec.primary = [Field(l, v) for (l, v) in clean]
    rec.flags = compute_flags(clean, status, call_date, stale_days)
    rec.head_directive = head_directive
    return rec


# --- Идентичность карточки (защита от дубликатов) ---
# ключ строится из устойчивых полей: ФИО + дата рождения, иначе ФИО + телефон;
# правка второстепенных полей идентичность не меняет

def _norm_name(name: str) -> str:
    s = (name or "").lower().replace("ё", "е")
    s = re.sub(r"[^0-9a-zа-я ]+", " ", s)
    return " ".join(s.split())


def _norm_phone(phone: str) -> str:
    d = re.sub(r"\D", "", phone or "")
    return d[-10:] if len(d) >= 10 else d


def card_identity(name: str, birth_date: str = "", phone: str = "") -> str:
    """Устойчивый ключ карточки; пустая строка — идентичность не определить."""
    nm = _norm_name(name)
    birth = re.sub(r"\D", "", birth_date or "")
    ph = _norm_phone(phone)
    if nm and birth:
        return f"nb:{nm}|{birth}"
    if nm and ph:
        return f"np:{nm}|{ph}"
    if nm:
        return f"n:{nm}"
    if ph:
        return f"p:{ph}"
    return ""


def record_identity(rec: "UsvoRecord") -> str:
    return card_identity(rec.name, rec.birth_date, rec.phone)
