"""Exportación de la Pokédex a CSV y PDF."""

import csv
import io
from collections.abc import Iterable, Mapping, Sequence
from datetime import UTC, datetime
from typing import Final

from reportlab.graphics.charts.spider import SpiderChart
from reportlab.graphics.shapes import Drawing
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.pdfgen.canvas import Canvas
from reportlab.platypus import (
    Image,
    PageBreak,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)
from reportlab.platypus.doctemplate import BaseDocTemplate

from app.models import PokedexEntry, StatBlock, TeamAnalysis, TeamRead
from app.services.card_renderer import darken, type_color, type_label
from app.services.team_analysis import STAT_KEYS, STAT_LABELS, es_number

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

RADAR_SCALE: Final = 150
RADAR_LABELS: Final = {
    "hp": "PS",
    "attack": "Ataque",
    "defense": "Defensa",
    "special_attack": "At. Esp.",
    "special_defense": "Def. Esp.",
    "speed": "Velocidad",
}
RADAR_LABELS_LONG: Final = STAT_LABELS


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


# ═════════════════════════════════════════════════════════════
#  PDF de un equipo
# ═════════════════════════════════════════════════════════════
def _radar(averages: StatBlock) -> Drawing:
    """Gráfico radar con la media de cada estadística del equipo.

    Se dibuja un contorno de referencia en 150 para que la escala sea siempre la misma y
    los equipos se puedan comparar a simple vista.
    """
    values = [getattr(averages, key) for key in STAT_KEYS]
    drawing = Drawing(80 * mm, 72 * mm)
    chart = SpiderChart()
    chart.x, chart.y = 14 * mm, 8 * mm
    chart.width, chart.height = 52 * mm, 52 * mm
    chart.data = [[RADAR_SCALE] * len(values), values]
    chart.labels = [
        f"{RADAR_LABELS[k]} {es_number(v)}" for k, v in zip(STAT_KEYS, values, strict=True)
    ]
    chart.strands[0].fillColor = None
    chart.strands[0].strokeColor = colors.HexColor("#D9D4C7")
    chart.strands[0].strokeWidth = 0.5
    chart.strands[1].fillColor = colors.Color(0.75, 0.22, 0.17, alpha=0.35)
    chart.strands[1].strokeColor = BRAND
    chart.strands[1].strokeWidth = 1.5
    chart.spokes.strokeColor = colors.HexColor("#E2DDD0")
    chart.spokeLabels.fontName = "Helvetica-Bold"
    chart.spokeLabels.fontSize = 7.5
    chart.spokeLabels.fillColor = INK
    drawing.add(chart)
    return drawing


_MULTIPLIER_TEXT: Final = {0: "x0", 0.25: "x1/4", 0.5: "x1/2", 1: "", 2: "x2", 4: "x4"}


def _multiplier_color(value: float) -> colors.Color | None:
    if value == 0:
        return colors.HexColor("#CFCFCF")
    if value > 1:
        return colors.HexColor("#F4B6B0") if value == 2 else colors.HexColor("#E57368")
    if value < 1:
        return colors.HexColor("#BFE3C0") if value == 0.5 else colors.HexColor("#8CCB8F")
    return None


def _table_style(*, bold_last_row: bool = False, stripes: bool = True) -> list[tuple]:
    commands: list[tuple] = [
        ("BACKGROUND", (0, 0), (-1, 0), BRAND),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONT", (0, 0), (-1, 0), "Helvetica-Bold", 9),
        ("FONT", (0, 1), (-1, -1), "Helvetica", 9),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LINEBELOW", (0, 0), (-1, -1), 0.25, colors.HexColor("#E2DDD0")),
        ("TOPPADDING", (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
    ]
    if stripes:
        commands.append(("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, STRIPE]))
    if bold_last_row:
        commands.append(("FONT", (0, -1), (-1, -1), "Helvetica-Bold", 9))
    return commands


def team_to_pdf(
    team: TeamRead,
    analysis: TeamAnalysis,
    cards: Sequence[bytes],
    sprites: Mapping[str, bytes] | None = None,
) -> bytes:
    """PDF del equipo en tres partes:

    1. Resumen: miembros, estadísticas combinadas con gráfico radar y conclusiones.
    2. Fichas de los Pokémon (las mismas cartas que ``/pokemon/{id}/card``).
    3. Cobertura de tipos: el daño que recibiría cada miembro de cada tipo de ataque.
    """
    sprites = sprites or {}
    styles = _styles()
    h2 = ParagraphStyle("h2", fontName="Helvetica-Bold", fontSize=14, textColor=INK, spaceAfter=6)
    body = ParagraphStyle("body", fontName="Helvetica", fontSize=9.5, leading=13, textColor=INK)
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        leftMargin=14 * mm,
        rightMargin=14 * mm,
        topMargin=14 * mm,
        bottomMargin=18 * mm,
        title=f"Equipo {team.name}",
        author="Pokédex API",
    )
    width = A4[0] - 28 * mm
    generated = datetime.now(UTC).strftime("%d/%m/%Y %H:%M UTC")

    story: list[object] = [
        Paragraph(_escape(team.name), styles["title"]),
        Paragraph(f"Equipo de batalla  ·  generado el {generated}", styles["meta"]),
        Spacer(1, 4 * mm),
    ]
    if team.description:
        story += [Paragraph(f"<i>{_escape(team.description)}</i>", body), Spacer(1, 4 * mm)]

    # Miembros
    rows: list[list[object]] = [["", "Pos.", "Pokémon", "Tipos", "Total base"]]
    for member, stats in zip(team.members, analysis.members, strict=True):
        sprite = sprites.get(member.sprite or "")
        name = f"<b>{_escape(member.pokemon_name.replace('-', ' ').title())}</b>"
        if member.nickname:
            name += f'<br/><font color="#5B6170">«{_escape(member.nickname)}»</font>'
        rows.append(
            [
                Image(io.BytesIO(sprite), width=11 * mm, height=11 * mm) if sprite else "",
                str(member.position),
                Paragraph(name, styles["cell"]),
                Paragraph(_types_markup(member.types), styles["cell"]),
                str(stats.base_stat_total),
            ]
        )
    members_table = Table(
        rows, colWidths=[14 * mm, 12 * mm, 52 * mm, 44 * mm, 24 * mm], repeatRows=1
    )
    members_table.setStyle(TableStyle(_table_style()))

    # Estadísticas combinadas y gráfico radar
    stats_rows: list[list[object]] = [["Estadística", "Total", "Media"]]
    for key in STAT_KEYS:
        stats_rows.append(
            [
                RADAR_LABELS_LONG[key],
                es_number(getattr(analysis.combined.total, key)),
                es_number(getattr(analysis.combined.average, key)),
            ]
        )
    stats_rows.append(
        [
            "Total base",
            str(analysis.combined.base_stat_total),
            es_number(analysis.combined.average_base_stat_total),
        ]
    )
    stats_table = Table(stats_rows, colWidths=[34 * mm, 18 * mm, 18 * mm])
    stats_table.setStyle(TableStyle(_table_style(bold_last_row=True)))

    story += [
        Paragraph("Miembros", h2),
        members_table,
        Spacer(1, 6 * mm),
        Paragraph("Estadísticas combinadas", h2),
        Table(
            [[stats_table, _radar(analysis.combined.average)]],
            colWidths=[76 * mm, width - 76 * mm],
            style=[("VALIGN", (0, 0), (-1, -1), "MIDDLE")],
        ),
        Spacer(1, 4 * mm),
        Paragraph("Conclusiones", h2),
        *[Paragraph(f"•  {_escape(note)}", body) for note in analysis.highlights],
    ]

    # Fichas
    if cards:
        gap = 3 * mm
        card_width = (width - 2 * gap) / 3
        card_height = card_width * 840 / 600
        images = [Image(io.BytesIO(card), width=card_width, height=card_height) for card in cards]
        grid = [images[i : i + 3] for i in range(0, len(images), 3)]
        grid[-1] = grid[-1] + [""] * (3 - len(grid[-1]))
        story += [
            PageBreak(),
            Paragraph("Fichas", h2),
            Table(
                grid,
                colWidths=[card_width + gap * 2 / 3] * 3,
                style=[
                    ("ALIGN", (0, 0), (-1, -1), "CENTER"),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
                ],
            ),
        ]

    # Cobertura de tipos
    names = [m.pokemon_name.replace("-", " ").title()[:10] for m in team.members]
    coverage_rows: list[list[object]] = [["Ataque", *names, "Débiles", "Resisten"]]
    cell_styles: list[tuple] = []
    for row, matchup in enumerate(analysis.defensive, start=1):
        coverage_rows.append(
            [
                Paragraph(_types_markup([matchup.type]), styles["cell"]),
                *[_MULTIPLIER_TEXT.get(m, f"x{m:g}") for m in matchup.multipliers],
                str(matchup.weak),
                str(matchup.resist + matchup.immune),
            ]
        )
        for col, value in enumerate(matchup.multipliers, start=1):
            color = _multiplier_color(value)
            if color is not None:
                cell_styles.append(("BACKGROUND", (col, row), (col, row), color))
        if matchup.type in analysis.shared_weaknesses:
            cell_styles.append(("TEXTCOLOR", (-2, row), (-2, row), BRAND))
            cell_styles.append(("FONT", (-2, row), (-2, row), "Helvetica-Bold", 9))

    member_width = (width - 26 * mm - 34 * mm) / max(len(names), 1)
    coverage = Table(
        coverage_rows,
        colWidths=[26 * mm, *[member_width] * len(names), 17 * mm, 17 * mm],
        repeatRows=1,
    )
    coverage.setStyle(
        TableStyle(
            [*_table_style(stripes=False), ("ALIGN", (1, 0), (-1, -1), "CENTER"), *cell_styles]
        )
    )

    covered = ", ".join(type_label(t) for t in analysis.super_effective_against) or "ninguno"
    missing = ", ".join(type_label(t) for t in analysis.not_covered) or "ninguno"
    story += [
        PageBreak(),
        Paragraph("Cobertura de tipos", h2),
        Paragraph(
            "Daño que recibiría cada miembro de un ataque de cada tipo. En rojo, "
            "debilidades (x2, x4); en verde, resistencias (x1/2, x1/4); en gris, inmunidades.",
            body,
        ),
        Spacer(1, 3 * mm),
        coverage,
        Spacer(1, 5 * mm),
        Paragraph(f"<b>Supereficaz contra:</b> {_escape(covered)}", body),
        Paragraph(f"<b>Sin cobertura ofensiva:</b> {_escape(missing)}", body),
    ]

    doc.build(story, onFirstPage=_footer, onLaterPages=_footer)
    return buffer.getvalue()
