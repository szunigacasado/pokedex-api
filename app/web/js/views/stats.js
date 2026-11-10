// Estadísticas de tu Pokédex en gráficos: progreso, rachas y reparto por tipos.

import { request } from "../api.js";
import { el, svg } from "../dom.js";
import { formatDate, percent, plural, typeClass, typeLabel } from "../format.js";
import { empty, loader, typeBadge } from "../ui.js";

/** Anillo de progreso en SVG (sin librerías). */
export function ring(value, label) {
  const radius = 52;
  const circumference = 2 * Math.PI * radius;
  const filled = Math.max(0, Math.min(100, value)) / 100;
  return el(
    "figure",
    { class: "ring" },
    svg(
      "svg",
      { viewBox: "0 0 120 120", width: 150, height: 150, role: "img", "aria-label": `${label}: ${percent(value, 1)}` },
      svg("circle", { class: "ring__track", cx: 60, cy: 60, r: radius }),
      svg("circle", {
        class: "ring__value",
        cx: 60,
        cy: 60,
        r: radius,
        "stroke-dasharray": `${circumference * filled} ${circumference}`,
        transform: "rotate(-90 60 60)",
      }),
      svg("text", { class: "ring__text", x: 60, y: 66, "text-anchor": "middle" }, percent(value)),
    ),
    el("figcaption", {}, label),
  );
}

function tile(icon, value, label) {
  return el("div", { class: "tile" }, el("span", { class: "tile__icon", "aria-hidden": "true" }, icon), el("span", { class: "tile__value" }, value), el("span", { class: "tile__label" }, label));
}

function typeBars(distribution) {
  const rows = Object.entries(distribution ?? {}).sort((a, b) => b[1] - a[1]);
  if (!rows.length) return el("p", { class: "muted" }, "Aún no hay datos.");
  const max = rows[0][1];
  return el(
    "ul",
    { class: "bars" },
    rows.map(([type, count]) =>
      el(
        "li",
        { class: "bars__row" },
        el("span", { class: "bars__label" }, typeLabel(type)),
        el("span", { class: "bars__track", "aria-hidden": "true" }, el("span", { class: `bars__fill ${typeClass(type)}`, style: { "--w": `${(count / max) * 100}%` } })),
        el("span", { class: "bars__count" }, String(count)),
      ),
    ),
  );
}

export async function renderStats(view) {
  view.replaceChildren(loader("Calculando tus estadísticas…"));
  const tz = Intl.DateTimeFormat().resolvedOptions().timeZone;
  const stats = await request("/api/v1/pokedex/stats", { query: { tz } });

  if (stats.total_pokemon === 0) {
    view.replaceChildren(
      el("h1", {}, "Estadísticas"),
      empty("📊", "Todavía no hay nada que contar", "Añade y captura Pokémon para ver aquí tu progreso.", el("a", { class: "btn btn--primary", href: "#/buscar" }, "🔍 Empezar a buscar")),
    );
    return;
  }

  view.replaceChildren(
    el("div", { class: "page-head" }, el("div", {}, el("h1", {}, "Estadísticas"), el("p", {}, `Calculadas con tu zona horaria (${stats.timezone}).`))),
    el(
      "div",
      { class: "stats-top" },
      ring(stats.completion_percentage, `Capturados de tu Pokédex (${stats.captured} de ${stats.total_pokemon})`),
      stats.national_dex_percentage !== null
        ? ring(stats.national_dex_percentage, `De la Pokédex nacional (${stats.national_dex_total ?? "?"} en total)`)
        : null,
      el(
        "div",
        { class: "tiles" },
        tile("🔥", plural(stats.capture_streak_days, "día"), "Racha actual de capturas"),
        tile("🏆", plural(stats.longest_streak_days, "día"), "Tu mejor racha"),
        tile("★", String(stats.favorites), "Favoritos"),
        el("div", { class: "tile" }, el("span", { class: "tile__icon", "aria-hidden": "true" }, "🧬"), stats.most_common_type ? typeBadge(stats.most_common_type) : el("span", { class: "tile__value" }, "—"), el("span", { class: "tile__label" }, "Tu tipo más común")),
      ),
    ),
    el("section", { class: "panel stack" }, el("h2", {}, "Tus Pokémon por tipo"), typeBars(stats.type_distribution)),
    stats.first_capture_date
      ? el("p", { class: "muted" }, `Primera captura: ${formatDate(stats.first_capture_date)} · Última: ${formatDate(stats.last_capture_date)}`)
      : null,
  );
}
