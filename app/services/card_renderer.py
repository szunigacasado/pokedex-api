"""Generación de la ficha descargable de un Pokémon (PNG o PDF) con Pillow.

La ficha imita una carta coleccionable:

- Color de fondo según el tipo principal.
- Nombre y categoría en español, número de Pokédex y puntos de salud.
- Ilustración oficial, tipos, las seis estadísticas con barras y habilidades.
- Descripción de la Pokédex, y una marca si el Pokémon es legendario o mítico.

Se usa la fuente libre Nunito (licencia OFL, incluida en ``app/assets/fonts``) porque la
fuente por defecto de Pillow no tiene acentos ni eñes.
"""

import io
from functools import lru_cache
from pathlib import Path
from typing import Final, Literal

from PIL import Image, ImageDraw, ImageFont, UnidentifiedImageError

from app.models import PokemonDetail, PokemonSpecies

CardFormat = Literal["png", "pdf"]

FONT_PATH: Final = (
    Path(__file__).resolve().parent.parent / "assets" / "fonts" / "Nunito-Variable.ttf"
)

WIDTH, HEIGHT = 600, 840
MARGIN = 18
MAX_STAT = 255  # valor base máximo posible de una estadística

TYPE_COLORS: Final = {
    "normal": "#A8A77A",
    "fire": "#EE8130",
    "water": "#6390F0",
    "electric": "#F7D02C",
    "grass": "#7AC74C",
    "ice": "#96D9D6",
    "fighting": "#C22E28",
    "poison": "#A33EA1",
    "ground": "#E2BF65",
    "flying": "#A98FF3",
    "psychic": "#F95587",
    "bug": "#A6B91A",
    "rock": "#B6A136",
    "ghost": "#735797",
    "dragon": "#6F35FC",
    "dark": "#705746",
    "steel": "#B7B7CE",
    "fairy": "#D685AD",
}
DEFAULT_TYPE_COLOR: Final = "#9E9E9E"

TYPE_NAMES_ES: Final = {
    "normal": "Normal",
    "fire": "Fuego",
    "water": "Agua",
    "electric": "Eléctrico",
    "grass": "Planta",
    "ice": "Hielo",
    "fighting": "Lucha",
    "poison": "Veneno",
    "ground": "Tierra",
    "flying": "Volador",
    "psychic": "Psíquico",
    "bug": "Bicho",
    "rock": "Roca",
    "ghost": "Fantasma",
    "dragon": "Dragón",
    "dark": "Siniestro",
    "steel": "Acero",
    "fairy": "Hada",
}

STAT_LABELS_ES: Final = (
    ("hp", "PS"),
    ("attack", "Ataque"),
    ("defense", "Defensa"),
    ("special_attack", "At. Esp."),
    ("special_defense", "Def. Esp."),
    ("speed", "Velocidad"),
)

INK = "#1F2430"
MUTED = "#5B6170"
PAPER = "#FFFDF6"
TRACK = "#E7E2D6"


# ═════════════════════════════════════════════════════════════
#  Utilidades de dibujo
# ═════════════════════════════════════════════════════════════
@lru_cache(maxsize=32)
def _font(size: int, weight: str = "Regular") -> ImageFont.FreeTypeFont:
    font = ImageFont.truetype(str(FONT_PATH), size)
    font.set_variation_by_name(weight)
    return font


def _hex_to_rgb(color: str) -> tuple[int, int, int]:
    color = color.lstrip("#")
    return int(color[0:2], 16), int(color[2:4], 16), int(color[4:6], 16)


def _mix(color: str, other: str, amount: float) -> tuple[int, int, int]:
    """Mezcla dos colores: ``amount=0`` devuelve ``color`` y ``1`` devuelve ``other``."""
    a, b = _hex_to_rgb(color), _hex_to_rgb(other)
    return tuple(round(x + (y - x) * amount) for x, y in zip(a, b, strict=True))  # type: ignore[return-value]


def darken(color: str, amount: float = 0.35) -> str:
    """Versión más oscura de un color, para escribir texto legible sobre fondo blanco."""
    return "#{:02X}{:02X}{:02X}".format(*_mix(color, "#000000", amount))


def _readable_text_on(color: str) -> str:
    r, g, b = _hex_to_rgb(color)
    luminance = 0.299 * r + 0.587 * g + 0.114 * b
    return INK if luminance > 170 else "#FFFFFF"


def type_color(type_name: str) -> str:
    return TYPE_COLORS.get(type_name, DEFAULT_TYPE_COLOR)


def type_label(type_name: str) -> str:
    return TYPE_NAMES_ES.get(type_name, type_name.replace("-", " ").title())


def _pretty(slug: str) -> str:
    return slug.replace("-", " ").title()


def _wrap(text: str, font: ImageFont.FreeTypeFont, max_width: int, max_lines: int) -> list[str]:
    """Parte el texto en líneas que caben en ``max_width``; si sobra, termina en «…».

    No se usa ``textwrap`` porque corta por número de caracteres, no por la anchura
    real en píxeles de una fuente proporcional.
    """
    if max_lines <= 0:
        return []
    words, lines, current = text.split(), [], ""
    for word in words:
        candidate = f"{current} {word}".strip()
        if font.getlength(candidate) <= max_width:
            current = candidate
            continue
        lines.append(current)
        current = word
        if len(lines) == max_lines:
            break
    else:
        if current:
            lines.append(current)
        return lines

    last = lines[-1]
    while last and font.getlength(last + "…") > max_width:
        last = last[:-1]
    lines[-1] = last.rstrip() + "…"
    return lines


def _centered(draw: ImageDraw.ImageDraw, y: int, text: str, font, fill: str) -> None:
    draw.text((WIDTH / 2, y), text, font=font, fill=fill, anchor="mt")


# ═════════════════════════════════════════════════════════════
#  Ficha
# ═════════════════════════════════════════════════════════════
def render_card(
    pokemon: PokemonDetail,
    species: PokemonSpecies | None = None,
    artwork: bytes | None = None,
    ability_names: dict[str, str] | None = None,
) -> Image.Image:
    """Dibuja la ficha y la devuelve como imagen RGB de 600 x 840 px.

    ``ability_names`` traduce los nombres de habilidad de PokeAPI (``static``) a su
    nombre en español (``Elec. Estática``). Si falta alguno, se muestra el original.
    """
    ability_names = ability_names or {}
    primary = type_color(pokemon.types[0] if pokemon.types else "")
    card = Image.new("RGB", (WIDTH, HEIGHT), _mix(primary, "#000000", 0.25))
    draw = ImageDraw.Draw(card)

    # Marco y cuerpo de la carta
    draw.rounded_rectangle(
        (MARGIN, MARGIN, WIDTH - MARGIN, HEIGHT - MARGIN),
        radius=26,
        fill=_mix(primary, "#FFFFFF", 0.15),
    )
    draw.rounded_rectangle(
        (MARGIN + 12, MARGIN + 12, WIDTH - MARGIN - 12, HEIGHT - MARGIN - 12), radius=18, fill=PAPER
    )

    # Cabecera: nombre, número y PS
    left, right = MARGIN + 32, WIDTH - MARGIN - 32
    name = species.display_name if species else _pretty(pokemon.name)
    draw.text((left, 46), name, font=_font(38, "Black"), fill=INK)
    draw.text(
        (right, 50),
        f"PS {pokemon.stats.hp}",
        font=_font(30, "ExtraBold"),
        fill="#C0392B",
        anchor="ra",
    )
    subtitle = f"N.º {pokemon.id:04d}"
    if species and species.genus:
        subtitle += f"  ·  {species.genus}"
    draw.text((left, 94), subtitle, font=_font(18, "SemiBold"), fill=MUTED)

    if species and (species.is_legendary or species.is_mythical):
        badge = "LEGENDARIO" if species.is_legendary else "MÍTICO"
        badge_font = _font(14, "ExtraBold")
        width = badge_font.getlength(badge) + 22
        draw.rounded_rectangle((right - width, 96, right, 120), radius=12, fill="#D4A017")
        draw.text((right - width / 2, 108), badge, font=badge_font, fill="#FFFFFF", anchor="mm")

    # Ilustración
    art_box = (left, 132, right, 382)
    draw.rounded_rectangle(art_box, radius=16, fill=_mix(primary, "#FFFFFF", 0.55))
    _paste_artwork(card, artwork, art_box)

    # Tipos
    badge_font = _font(18, "ExtraBold")
    labels = [(type_label(t), type_color(t)) for t in pokemon.types]
    widths = [badge_font.getlength(label) + 36 for label, _ in labels]
    x = WIDTH / 2 - (sum(widths) + 12 * (len(widths) - 1)) / 2
    for (label, color), width in zip(labels, widths, strict=True):
        draw.rounded_rectangle((x, 396, x + width, 430), radius=17, fill=color)
        draw.text(
            (x + width / 2, 413), label, font=badge_font, fill=_readable_text_on(color), anchor="mm"
        )
        x += width + 12

    # Altura y peso
    measures = f"Altura {pokemon.height / 10:.1f} m   ·   Peso {pokemon.weight / 10:.1f} kg"
    _centered(draw, 442, measures.replace(".", ","), _font(16, "SemiBold"), MUTED)

    # Estadísticas
    label_font, value_font = _font(17, "Bold"), _font(17, "ExtraBold")
    bar_left, bar_right = left + 150, right - 6
    y = 478
    for key, label in STAT_LABELS_ES:
        value = getattr(pokemon.stats, key)
        draw.text((left, y), label, font=label_font, fill=INK, anchor="lm")
        draw.text((bar_left - 14, y), str(value), font=value_font, fill=INK, anchor="rm")
        draw.rounded_rectangle((bar_left, y - 7, bar_right, y + 7), radius=7, fill=TRACK)
        filled = bar_left + (bar_right - bar_left) * min(value, MAX_STAT) / MAX_STAT
        draw.rounded_rectangle(
            (bar_left, y - 7, max(filled, bar_left + 14), y + 7), radius=7, fill=primary
        )
        y += 27
    draw.text(
        (left, y + 2),
        f"Total {pokemon.stats.total}",
        font=_font(15, "Bold"),
        fill=MUTED,
        anchor="lm",
    )

    # Habilidades
    abilities = ", ".join(
        ability_names.get(a.name, _pretty(a.name)) + (" (oculta)" if a.is_hidden else "")
        for a in pokemon.abilities
    )
    ability_lines = _wrap(f"Habilidades: {abilities}", _font(16, "SemiBold"), right - left, 2)
    y += 26
    for line in ability_lines:
        draw.text((left, y), line, font=_font(16, "SemiBold"), fill=INK)
        y += 21

    # Descripción de la Pokédex, con las líneas que quepan antes del pie
    footer_y = HEIGHT - MARGIN - 34
    if species and species.description:
        y += 8
        draw.line((left, y, right, y), fill=TRACK, width=2)
        y += 10
        max_lines = max(0, (footer_y - 8 - y) // 21)
        for line in _wrap(species.description, _font(16), right - left, max_lines):
            draw.text((left, y), line, font=_font(16), fill=INK)
            y += 21

    _centered(draw, footer_y, "Pokédex API  ·  datos de PokeAPI", _font(13, "SemiBold"), MUTED)
    return card


def _paste_artwork(
    card: Image.Image, artwork: bytes | None, box: tuple[int, int, int, int]
) -> None:
    left, top, right, bottom = box
    draw = ImageDraw.Draw(card)
    if artwork:
        try:
            image = Image.open(io.BytesIO(artwork))
            image.load()
        except (UnidentifiedImageError, OSError):
            image = None
        if image is not None:
            image = image.convert("RGBA")
            image.thumbnail((right - left - 30, bottom - top - 20), Image.Resampling.LANCZOS)
            x = left + (right - left - image.width) // 2
            y = top + (bottom - top - image.height) // 2
            card.paste(image, (x, y), image)
            return
    draw.text(
        ((left + right) / 2, (top + bottom) / 2),
        "Imagen no disponible",
        font=_font(20, "SemiBold"),
        fill=MUTED,
        anchor="mm",
    )


def encode_card(card: Image.Image, fmt: CardFormat) -> bytes:
    buffer = io.BytesIO()
    if fmt == "pdf":
        card.save(buffer, format="PDF", resolution=150, title="Ficha Pokédex")
    else:
        card.save(buffer, format="PNG", optimize=True)
    return buffer.getvalue()


def card_filename(pokemon: PokemonDetail, fmt: CardFormat) -> str:
    safe = "".join(c for c in pokemon.name if c.isalnum() or c == "-")[:50] or "pokemon"
    return f"{pokemon.id:04d}-{safe}-ficha.{fmt}"
