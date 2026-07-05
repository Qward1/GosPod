"""Извлечение данных из Excel-таблицы в компактный текст для LLM.

Задача: качественно превратить широкую разрежённую таблицу (в примере 34 столбца ×
50 строк, много пустых ячеек) в текст, который:
  - содержит ВСЕ данные таблицы (требование задания);
  - максимально компактен — чтобы влезть в лимит входа LLM 32k токенов.

Ключевая идея экономии: длинные названия столбцов (напр. "Примечание по состоянию
здоровья (ампутация, увечие, тяжелые заболевания)") выписываются ОДИН раз в легенду,
а в записях используется числовой индекс столбца. Пустые ячейки пропускаются.

Формат вывода:

    Легенда столбцов:
    [1] Дата обзвона
    [2] № номерация
    [3] ФИО УСВО
    ...

    Записи (формат "[индекс] значение", пустые поля пропущены):

    Запись 1: [1] 12.12.2025; [3] Абубакар Арнольд Аркадьевич; [5] комиссован; ...
    Запись 2: ...

Даты приводятся к ДД.ММ.ГГГГ (в т.ч. "серийные" числа Excel — по числовому формату).
"""
from __future__ import annotations

import datetime as dt
import io
from dataclasses import dataclass

import openpyxl
from openpyxl.utils.datetime import from_excel


@dataclass
class ExtractResult:
    text: str
    rows: int          # сколько записей реально включено в текст
    total_rows: int    # сколько строк с данными было в таблице
    columns: list[str]
    char_count: int
    token_estimate: int
    truncated: bool


def _is_date_format(number_format: str | None) -> bool:
    if not number_format:
        return False
    fmt = number_format.lower()
    if fmt in ("general", "@"):
        return False
    return any(token in fmt for token in ("y", "d", "m")) and "0" not in fmt.replace("m", "")


def _format_value(value, number_format: str | None) -> str:
    if value is None:
        return ""
    if isinstance(value, (dt.datetime, dt.date)):
        return value.strftime("%d.%m.%Y")
    if isinstance(value, (int, float)) and _is_date_format(number_format):
        try:
            converted = from_excel(value)
            if isinstance(converted, (dt.datetime, dt.date)):
                return converted.strftime("%d.%m.%Y")
        except Exception:
            pass
    text = str(value).strip()
    # Многострочные ячейки сжимаем в одну строку.
    text = " ".join(part.strip() for part in text.splitlines() if part.strip())
    return text


def _estimate_tokens(char_count: int) -> int:
    # Грубая консервативная оценка для кириллицы: ~2 символа на токен.
    return char_count // 2


def _clean_header(value: str, index: int) -> str:
    text = " ".join(str(value or "").split())
    return text or f"Колонка {index}"


def extract_xlsx(
    file_bytes: bytes,
    max_chars: int = 60000,
    drop_empty_cells: bool = True,
) -> ExtractResult:
    """Парсит xlsx и возвращает компактный текст со всеми записями.

    Если суммарный текст превышает max_chars, лишние записи отбрасываются,
    выставляется truncated=True и в текст добавляется честное предупреждение.
    """
    wb = openpyxl.load_workbook(io.BytesIO(file_bytes), data_only=True, read_only=True)

    legend_columns: list[str] = []   # заголовки первого листа (для поля columns)
    record_blocks: list[str] = []    # по одной строке на запись
    legend_text_parts: list[str] = []
    record_index = 0
    total_data_rows = 0
    multi_sheet = len(wb.worksheets) > 1

    for ws in wb.worksheets:
        rows = ws.iter_rows()
        try:
            header_cells = next(rows)
        except StopIteration:
            continue

        headers = [_clean_header(c.value, i + 1) for i, c in enumerate(header_cells)]
        if not legend_columns:
            legend_columns = headers

        # Легенда столбцов листа.
        if multi_sheet:
            legend_text_parts.append(f"=== Лист «{ws.title}» ===")
        legend_text_parts.append("Легенда столбцов:")
        legend_text_parts.extend(f"[{i + 1}] {h}" for i, h in enumerate(headers))
        legend_text_parts.append("")

        for row in rows:
            pairs = []
            for i, cell in enumerate(row):
                v = _format_value(cell.value, cell.number_format)
                if v == "" and drop_empty_cells:
                    continue
                if v == "":
                    continue
                pairs.append(f"[{i + 1}] {v}")

            if not pairs:
                continue

            total_data_rows += 1
            record_index += 1
            record_blocks.append(f"Запись {record_index}: " + "; ".join(pairs))

    wb.close()

    legend_text = "\n".join(legend_text_parts).rstrip()
    intro = '\nЗаписи (формат "[индекс] значение", пустые поля пропущены):\n'

    # Бюджет символов: сперва легенда + заголовок, затем записи.
    base = legend_text + "\n" + intro
    running = len(base)
    included = 0
    out_records: list[str] = []
    truncated = False

    for block in record_blocks:
        addition = len(block) + 1  # +1 на перевод строки
        if running + addition > max_chars:
            truncated = True
            break
        out_records.append(block)
        running += addition
        included += 1

    text = base + "\n".join(out_records)

    if truncated:
        text += (
            f"\n\n[ВНИМАНИЕ: показаны {included} из {total_data_rows} записей — "
            f"данные усечены под лимит модели. Уточните вопрос, чтобы сузить выборку.]"
        )

    char_count = len(text)
    return ExtractResult(
        text=text,
        rows=included,
        total_rows=total_data_rows,
        columns=legend_columns,
        char_count=char_count,
        token_estimate=_estimate_tokens(char_count),
        truncated=truncated,
    )
