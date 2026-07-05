"""Работа с DOCX-шаблонами мер поддержки (без сторонних библиотек, только stdlib).

Шаблон — обычный .docx с размеченными плейсхолдерами вида ``{{snake_case}}``
(формат понятен и человеку, и LLM; тот же стиль, что в document_assistant.yml).

Тут три функции:
  • extract_placeholders(docx_bytes) — список ключей, размеченных в шаблоне;
  • fill_template_docx(docx_bytes, values) — подставляет значения и отдаёт .docx;
  • check_required(placeholders, values) — что заполнено, а что осталось пустым.

Word нередко разбивает один плейсхолдер на несколько ``<w:t>``-прогонов (runs),
поэтому текст склеивается в пределах абзаца ``<w:p>`` перед поиском/заменой.
"""
from __future__ import annotations

import io
import re
import zipfile
from xml.sax.saxutils import escape, unescape

# Файлы внутри .docx, где может быть размеченный текст (тело + колонтитулы).
_TEXT_PARTS = re.compile(r"word/(document|header\d*|footer\d*)\.xml$")
_PLACEHOLDER = re.compile(r"\{\{\s*([A-Za-z0-9_]+)\s*\}\}")
_PARAGRAPH = re.compile(r"<w:p\b[^>]*>.*?</w:p>", re.DOTALL)
_RUN_TEXT = re.compile(r"(<w:t\b[^>]*>)(.*?)(</w:t>)", re.DOTALL)


def _paragraph_text(p_xml: str) -> str:
    """Склеивает текст всех runs абзаца (с раскодированными XML-сущностями)."""
    return "".join(unescape(m.group(2)) for m in _RUN_TEXT.finditer(p_xml))


def extract_placeholders(docx_bytes: bytes) -> list[str]:
    """Возвращает уникальные ключи плейсхолдеров шаблона в порядке появления."""
    keys: list[str] = []
    seen: set[str] = set()
    try:
        with zipfile.ZipFile(io.BytesIO(docx_bytes)) as z:
            names = [n for n in z.namelist() if _TEXT_PARTS.search(n)]
            for name in names:
                xml = z.read(name).decode("utf-8", errors="ignore")
                for p_xml in _PARAGRAPH.findall(xml):
                    for m in _PLACEHOLDER.finditer(_paragraph_text(p_xml)):
                        key = m.group(1)
                        if key not in seen:
                            seen.add(key)
                            keys.append(key)
    except (zipfile.BadZipFile, KeyError, OSError):
        return []
    return keys


def _replace_placeholders(text: str, values: dict) -> str:
    """Заменяет ``{{key}}`` на значение (неизвестные ключи → пустая строка)."""
    def sub(m: re.Match) -> str:
        return str(values.get(m.group(1), "") or "")
    return _PLACEHOLDER.sub(sub, text)


def _fill_paragraph(p_xml: str, values: dict) -> str:
    """Подставляет значения в абзац: весь текст уходит в первый run, остальные пустеют."""
    runs = list(_RUN_TEXT.finditer(p_xml))
    if not runs:
        return p_xml
    combined = _paragraph_text(p_xml)
    if "{{" not in combined:
        return p_xml
    replaced = _replace_placeholders(combined, values)

    out: list[str] = []
    last = 0
    for i, m in enumerate(runs):
        out.append(p_xml[last:m.start()])
        if i == 0:
            # xml:space="preserve" — чтобы не схлопнулись ведущие/хвостовые пробелы.
            out.append('<w:t xml:space="preserve">' + escape(replaced) + "</w:t>")
        else:
            out.append(m.group(1) + "</w:t>")
        last = m.end()
    out.append(p_xml[last:])
    return "".join(out)


def fill_template_docx(docx_bytes: bytes, values: dict) -> bytes:
    """Заполняет шаблон значениями плейсхолдеров и возвращает байты .docx."""
    src = zipfile.ZipFile(io.BytesIO(docx_bytes))
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as dst:
        for item in src.infolist():
            data = src.read(item.filename)
            if _TEXT_PARTS.search(item.filename):
                xml = data.decode("utf-8", errors="ignore")
                xml = _PARAGRAPH.sub(lambda m: _fill_paragraph(m.group(0), values), xml)
                data = xml.encode("utf-8")
            dst.writestr(item, data)
    src.close()
    return buf.getvalue()


def check_required(placeholders: list[str], values: dict) -> dict:
    """Сравнивает плейсхолдеры шаблона с заполненными значениями.

    Возвращает {"filled_fields": {key: value}, "missing_fields": [key, ...]}.
    Пустые/пробельные значения считаются незаполненными.
    """
    filled: dict[str, str] = {}
    missing: list[str] = []
    for key in placeholders:
        val = str(values.get(key, "") or "").strip()
        if val:
            filled[key] = val
        else:
            missing.append(key)
    return {"filled_fields": filled, "missing_fields": missing}
