"""Генерация .docx без сторонних зависимостей (только stdlib zipfile).

Нужно, чтобы микросервис мог сам собрать заполненное заявление в формате Word и
отдать его на скачивание в админ-разделе «Заявления» / отправить заявителю — даже
когда Dify-ассистент с плагином document-generator недоступен (офлайн-фолбэк).

API намеренно простое: список блоков — абзац (текст + стиль обычный/заголовок/жирный)
или таблица (строки ячеек с тонкими рамками). Таблица нужна для табличной справки
раздела «Час с ИИ» (заголовок «Информация на ДД.ММ.ГГГГ г.» + «№ | показатель | значение»).
"""
from __future__ import annotations

import io
import zipfile
from xml.sax.saxutils import escape

_CONTENT_TYPES = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
  <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
  <Default Extension="xml" ContentType="application/xml"/>
  <Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>
</Types>"""

_RELS = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>
</Relationships>"""

_DOC_RELS = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"></Relationships>"""

_NS = (
    'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main" '
    'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"'
)


def _run(text: str, *, bold: bool = False, size: int | None = None) -> str:
    props = []
    if bold:
        props.append("<w:b/>")
    if size:
        props.append(f'<w:sz w:val="{size * 2}"/>')  # half-points
    rpr = f"<w:rPr>{''.join(props)}</w:rPr>" if props else ""
    return f'{rpr}<w:t xml:space="preserve">{escape(text)}</w:t>'


def _paragraph(text: str, *, bold: bool = False, size: int | None = None,
               align: str | None = None) -> str:
    ppr = f'<w:pPr><w:jc w:val="{align}"/></w:pPr>' if align else ""
    if not text:
        return f"<w:p>{ppr}</w:p>"
    return f"<w:p>{ppr}<w:r>{_run(text, bold=bold, size=size)}</w:r></w:p>"


class Paragraph:
    __slots__ = ("text", "bold", "size", "align")

    def __init__(self, text: str = "", *, bold: bool = False,
                 size: int | None = None, align: str | None = None):
        self.text = text
        self.bold = bold
        self.size = size
        self.align = align


class Table:
    """Простая таблица: список строк, каждая строка — список ячеек (строк текста).

    Первая строка — шапка (жирная), если ``header=True``. ``widths`` — ширины колонок
    в твипах (dxa); если не заданы — авто. Рамки тонкие одинарные по всем границам.
    """

    __slots__ = ("rows", "header", "widths")

    def __init__(self, rows: list[list[str]], *, header: bool = True,
                 widths: list[int] | None = None):
        self.rows = rows
        self.header = header
        self.widths = widths


_TBL_BORDER = (
    '<w:tblBorders>'
    '<w:top w:val="single" w:sz="4" w:space="0" w:color="auto"/>'
    '<w:left w:val="single" w:sz="4" w:space="0" w:color="auto"/>'
    '<w:bottom w:val="single" w:sz="4" w:space="0" w:color="auto"/>'
    '<w:right w:val="single" w:sz="4" w:space="0" w:color="auto"/>'
    '<w:insideH w:val="single" w:sz="4" w:space="0" w:color="auto"/>'
    '<w:insideV w:val="single" w:sz="4" w:space="0" w:color="auto"/>'
    '</w:tblBorders>'
)


def _table(table: Table) -> str:
    grid = ""
    if table.widths:
        cols = "".join(f'<w:gridCol w:w="{int(w)}"/>' for w in table.widths)
        grid = f"<w:tblGrid>{cols}</w:tblGrid>"
    rows_xml = []
    for r, cells in enumerate(table.rows):
        bold = table.header and r == 0
        cells_xml = []
        for c, cell in enumerate(cells):
            tc_pr = ""
            if table.widths and c < len(table.widths):
                tc_pr = (f'<w:tcPr><w:tcW w:w="{int(table.widths[c])}" '
                         f'w:type="dxa"/></w:tcPr>')
            cells_xml.append(
                f"<w:tc>{tc_pr}{_paragraph(str(cell), bold=bold)}</w:tc>"
            )
        rows_xml.append(f"<w:tr>{''.join(cells_xml)}</w:tr>")
    return (
        f'<w:tbl><w:tblPr><w:tblW w:w="0" w:type="auto"/>{_TBL_BORDER}</w:tblPr>'
        f"{grid}{''.join(rows_xml)}</w:tbl>"
    )


def _render_block(block) -> str:
    if isinstance(block, Table):
        return _table(block)
    return _paragraph(block.text, bold=block.bold, size=block.size, align=block.align)


def build_docx(paragraphs: "list[Paragraph | Table]") -> bytes:
    """Собирает .docx из блоков (абзацы и таблицы) и возвращает байты файла."""
    body = "".join(_render_block(block) for block in paragraphs)
    document = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        f"<w:document {_NS}><w:body>{body}"
        '<w:sectPr><w:pgSz w:w="11906" w:h="16838"/>'
        '<w:pgMar w:top="1134" w:right="850" w:bottom="1134" w:left="1701"/>'
        "</w:sectPr></w:body></w:document>"
    )

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", _CONTENT_TYPES)
        z.writestr("_rels/.rels", _RELS)
        z.writestr("word/_rels/document.xml.rels", _DOC_RELS)
        z.writestr("word/document.xml", document)
    return buf.getvalue()
