"""Exportación de la Pokédex a CSV y PDF."""

import csv
import io
from collections.abc import Iterable, Mapping
from datetime import UTC, datetime
from typing import Final

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.pdfgen.canvas import Canvas
from reportlab.platypus import Image, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle
from reportlab.platypus.doctemplate import BaseDocTemplate

from app.models import PokedexEntry
from app.services.card_renderer import darken, type_color, type_label

CSV_COLUMNS: Final = (
    "entrada_id",
    "pokemon_id",
    "nombre",
    "apodo",
    "tipos",
    "capturado",
    "fecha_captura",
    "favorito",
    "notas",
    "anadido",
)

# Una celda que empieza por estos caracteres se interpreta como fórmula en Excel o en
# LibreOffice. Un apodo como «=HYPERLINK("http://malicioso", "clic")» se convertiría en
# un enlace o en una orden al abrir el CSV (inyección CSV, OWASP).
_FORMULA_PREFIXES: Final = ("=", "+", "-", "@", "\t", "\r")

BRAND: Final = colors.HexColor("#C0392B")
INK: Final = colors.HexColor("#1F2430")
MUTED: Final = colors.HexColor("#5B6170")
STRIPE: Final = colors.HexColor("#F6F3EA")


def _safe_cell(value: object) -> str:
    text = "" if value is None else str(value)
    return "'" + text if text.startswith(_FORMULA_PREFIXES) else text


def _date(value: datetime | None) -> str:
    return value.astimezone(UTC).strftime("%Y-%m-%d %H:%M") if value else ""


# ═════════════════════════════════════════════════════════════
#  CSV
# ═════════════════════════════════════════════════════════════
def pokedex_to_csv(entries: Iterable[PokedexEntry]) -> bytes:
    """CSV estándar (RFC 4180, separador coma) en UTF-8 con BOM, para que Excel
    reconozca los acentos al abrirlo con doble clic."""
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\r\n")
    writer.writerow(CSV_COLUMNS)
    for entry in entries:
        writer.writerow(
            [
                entry.id,
                entry.pokemon_id,
                _safe_cell(entry.pokemon_name),
                _safe_cell(entry.nickname),
                "|".join(entry.pokemon_types),
                "si" if entry.is_captured else "no",
                _date(entry.capture_date),
                "si" if entry.favorite else "no",
                _safe_cell(entry.notes),
                _date(entry.created_at),
            ]
        )
    return buffer.getvalue().encode("utf-8-sig")


# ═════════════════════════════════════════════════════════════
#  PDF
# ═════════════════════════════════════════════════════════════
def _styles() -> dict[str, ParagraphStyle]:
    base = getSampleStyleSheet()
    return {
        "title": ParagraphStyle(
            "title", parent=base["Title"], fontName="Helvetica-Bold", fontSize=22, textColor=INK
        ),
        "meta": ParagraphStyle(
            "meta", parent=base["Normal"], fontSize=9, textColor=MUTED, alignment=TA_CENTER
        ),
        "cell": ParagraphStyle("cell", parent=base["Normal"], fontSize=8.5, leading=10.5),
        "cell_bold": ParagraphStyle(
            "cell_bold", parent=base["Normal"], fontName="Helvetica-Bold", fontSize=9, leading=11
        ),
        "empty": ParagraphStyle(
            "empty", parent=base["Normal"], fontSize=11, textColor=MUTED, alignment=TA_CENTER
        ),
    }


def _escape(text: str | None) -> str:
    """Paragraph interpreta etiquetas tipo HTML; el texto del usuario se escapa."""
    if not text:
        return ""
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _types_markup(types: list[str]) -> str:
    return " ".join(
        f'<font color="{darken(type_color(t))}"><b>{_escape(type_label(t))}</b></font>'
        for t in types
    )


def _filters_text(filters: Mapping[str, bool | None]) -> str:
    labels = {
        "captured": ("capturados", "no capturados"),
        "favorite": ("favoritos", "no favoritos"),
    }
    parts = [labels[k][0 if v else 1] for k, v in filters.items() if v is not None and k in labels]
    return "Filtro: " + " y ".join(parts) if parts else "Pokédex completa"


def _footer(canvas: Canvas, doc: BaseDocTemplate) -> None:
    canvas.saveState()
    canvas.setFont("Helvetica", 8)
    canvas.setFillColor(MUTED)
    canvas.drawString(doc.leftMargin, 10 * mm, "Pokédex API  ·  datos de PokeAPI")
    canvas.drawRightString(A4[0] - doc.rightMargin, 10 * mm, f"Página {doc.page}")
    canvas.restoreState()


def pokedex_to_pdf(
    entries: list[PokedexEntry],
    username: str,
    sprites: Mapping[str, bytes] | None = None,
    filters: Mapping[str, bool | None] | None = None,
) -> bytes:
    """PDF A4 con resumen y una tabla paginada (la cabecera se repite en cada página)."""
    sprites = sprites or {}
    styles = _styles()
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        leftMargin=14 * mm,
        rightMargin=14 * mm,
        topMargin=14 * mm,
        bottomMargin=18 * mm,
        title=f"Pokédex de {username}",
        author="Pokédex API",
    )

    captured = sum(e.is_captured for e in entries)
    favorites = sum(e.favorite for e in entries)
    generated = datetime.now(UTC).strftime("%d/%m/%Y %H:%M UTC")
    story = [
        Paragraph(f"Pokédex de {_escape(username)}", styles["title"]),
        Paragraph(f"{_filters_text(filters or {})}  ·  generado el {generated}", styles["meta"]),
        Spacer(1, 5 * mm),
        _summary_table(len(entries), captured, favorites),
        Spacer(1, 6 * mm),
    ]

    if not entries:
        story.append(Paragraph("No hay ningún Pokémon que cumpla los filtros.", styles["empty"]))
    else:
        story.append(_entries_table(entries, sprites, styles))

    doc.build(story, onFirstPage=_footer, onLaterPages=_footer)
    return buffer.getvalue()


def _summary_table(total: int, captured: int, favorites: int) -> Table:
    data = [
        [str(total), str(captured), str(favorites)],
        ["Pokémon", "Capturados", "Favoritos"],
    ]
    table = Table(data, colWidths=[40 * mm] * 3)
    table.setStyle(
        TableStyle(
            [
                ("FONT", (0, 0), (-1, 0), "Helvetica-Bold", 20),
                ("TEXTCOLOR", (0, 0), (-1, 0), BRAND),
                ("FONT", (0, 1), (-1, 1), "Helvetica", 9),
                ("TEXTCOLOR", (0, 1), (-1, 1), MUTED),
                ("ALIGN", (0, 0), (-1, -1), "CENTER"),
                ("BOTTOMPADDING", (0, 0), (-1, 0), 2),
            ]
        )
    )
    return table


def _entries_table(
    entries: list[PokedexEntry], sprites: Mapping[str, bytes], styles: dict[str, ParagraphStyle]
) -> Table:
    header = ["", "N.º", "Pokémon", "Tipos", "Capturado", "Fav.", "Notas"]
    rows: list[list[object]] = [header]
    for entry in entries:
        sprite = sprites.get(entry.pokemon_sprite or "")
        name = f"<b>{_escape(entry.pokemon_name.replace('-', ' ').title())}</b>"
        if entry.nickname:
            name += f'<br/><font color="#5B6170">«{_escape(entry.nickname)}»</font>'
        rows.append(
            [
                Image(io.BytesIO(sprite), width=11 * mm, height=11 * mm) if sprite else "",
                f"{entry.pokemon_id:04d}",
                Paragraph(name, styles["cell"]),
                Paragraph(_types_markup(entry.pokemon_types), styles["cell"]),
                (
                    entry.capture_date.astimezone(UTC).strftime("%d/%m/%Y")
                    if entry.capture_date
                    else "—"
                )
                if entry.is_captured
                else "No",
                "Sí" if entry.favorite else "",
                Paragraph(_escape(entry.notes), styles["cell"]),
            ]
        )

    widths = [14 * mm, 13 * mm, 38 * mm, 32 * mm, 22 * mm, 10 * mm]
    widths.append(A4[0] - 28 * mm - sum(widths))
    table = Table(rows, colWidths=widths, repeatRows=1)
    table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), BRAND),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                ("FONT", (0, 0), (-1, 0), "Helvetica-Bold", 9),
                ("FONT", (0, 1), (-1, -1), "Helvetica", 9),
                ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, STRIPE]),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("ALIGN", (1, 0), (1, -1), "CENTER"),
                ("ALIGN", (4, 0), (5, -1), "CENTER"),
                ("LINEBELOW", (0, 0), (-1, -1), 0.25, colors.HexColor("#E2DDD0")),
                ("TOPPADDING", (0, 0), (-1, -1), 3),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
            ]
        )
    )
    return table
