// Gráficos en SVG y HTML, sin librerías: radar de estadísticas y mapa de debilidades.

import { el, svg } from "./dom.js";
import { STAT_LABELS, displayName, multiplierClass, multiplierLabel, multiplierMeaning, typeClass, typeLabel } from "./format.js";

const RADAR_MAX = 150; // escala fija: así se pueden comparar equipos entre sí

/** Radar de las seis estadísticas medias. */
export function radar(stats, { size = 300 } = {}) {
  const keys = Object.keys(STAT_LABELS);
  const center = size / 2;
  const radius = size / 2 - 48;
  const point = (index, value) => {
    const angle = -Math.PI / 2 + (index * 2 * Math.PI) / keys.length;
    const r = (Math.min(value, RADAR_MAX) / RADAR_MAX) * radius;
    return [center + r * Math.cos(angle), center + r * Math.sin(angle)];
  };
  const ring = (fraction) => keys.map((_, i) => point(i, RADAR_MAX * fraction).join(",")).join(" ");
  const values = keys.map((key, i) => point(i, stats[key] ?? 0).join(",")).join(" ");
  const description = keys.map((key) => `${STAT_LABELS[key]} ${Math.round(stats[key] ?? 0)}`).join(", ");

  return svg(
    "svg",
    { class: "radar", viewBox: `0 0 ${size} ${size}`, role: "img", "aria-label": `Estadísticas medias: ${description}` },
    [0.25, 0.5, 0.75, 1].map((f) => svg("polygon", { class: "radar__grid", points: ring(f) })),
    keys.map((_, i) => {
      const [x, y] = point(i, RADAR_MAX);
      return svg("line", { class: "radar__axis", x1: center, y1: center, x2: x, y2: y });
    }),
    svg("polygon", { class: "radar__area", points: values }),
    keys.map((key, i) => {
      const [x, y] = point(i, RADAR_MAX * 1.2);
      return svg(
        "text",
        { class: "radar__label", x, y, "text-anchor": "middle", "dominant-baseline": "middle" },
        svg("tspan", { x, dy: "-0.5em" }, STAT_LABELS[key]),
        svg("tspan", { x, dy: "1.2em", class: "radar__value" }, String(Math.round(stats[key] ?? 0))),
      );
    }),
  );
}

/**
 * Mapa de calor: una fila por tipo de ataque y una columna por miembro, con el
 * multiplicador de daño que recibiría. Tabla HTML real: se lee bien con lector de pantalla.
 */
export function weaknessHeatmap(analysis) {
  const members = analysis.members;
  return el(
    "div",
    { class: "heatmap-wrap" },
    el(
      "table",
      { class: "heatmap" },
      el("caption", { class: "visually-hidden" }, "Daño que recibe cada miembro de cada tipo de ataque"),
      el(
        "thead",
        {},
        el(
          "tr",
          {},
          el("th", { scope: "col" }, "Ataque"),
          members.map((m) => el("th", { scope: "col" }, displayName(m.name))),
          el("th", { scope: "col" }, "Resumen"),
        ),
      ),
      el(
        "tbody",
        {},
        analysis.defensive.map((row) =>
          el(
            "tr",
            {},
            el("th", { scope: "row" }, el("span", { class: `type ${typeClass(row.type)}` }, typeLabel(row.type))),
            row.multipliers.map((value, i) =>
              el(
                "td",
                { class: `mult ${multiplierClass(value)}`, title: `${displayName(members[i].name)}: ${multiplierMeaning(value)}` },
                value === 1 ? el("span", { class: "visually-hidden" }, "×1") : multiplierLabel(value),
              ),
            ),
            el(
              "td",
              { class: "heatmap__summary" },
              row.weak ? el("span", { class: "danger" }, `${row.weak} débil${row.weak > 1 ? "es" : ""}`) : null,
              row.weak && (row.resist || row.immune) ? " · " : null,
              row.resist + row.immune ? el("span", { class: "ok" }, `${row.resist + row.immune} resiste${row.resist + row.immune > 1 ? "n" : ""}`) : null,
            ),
          ),
        ),
      ),
    ),
    el(
      "p",
      { class: "heatmap__legend muted" },
      el("span", { class: "mult mult--x4" }, "×4"),
      el("span", { class: "mult mult--x2" }, "×2"),
      el("span", { class: "mult mult--half" }, "×½"),
      el("span", { class: "mult mult--quarter" }, "×¼"),
      el("span", { class: "mult mult--immune" }, "×0"),
      " Rojo: le hace mucho daño. Verde: lo resiste. Gris: no le afecta.",
    ),
  );
}
