// Iconos de la interfaz: SVG propios de trazo (24×24), con el color del texto. Sustituyen
// a los emojis, que cada sistema dibuja distinto. Son decorativos (aria-hidden): el texto
// del botón o una etiqueta dicen lo que hacen.

import { svg } from "./dom.js";

const STAR = "12 3 14.9 8.9 21.5 9.8 16.7 14.4 17.9 21 12 17.8 6.1 21 7.3 14.4 2.5 9.8 9.1 8.9";

function gear() {
  const ticks = [];
  for (let i = 0; i < 8; i += 1) {
    const a = (i * Math.PI) / 4;
    const p = (r) => [12 + r * Math.cos(a), 12 + r * Math.sin(a)].map((n) => n.toFixed(2));
    const [x1, y1] = p(7.5);
    const [x2, y2] = p(10);
    ticks.push(["line", { x1, y1, x2, y2 }]);
  }
  return [["circle", { cx: 12, cy: 12, r: 3 }], ["circle", { cx: 12, cy: 12, r: 6.5 }], ...ticks];
}

/** Cada icono es una lista de [etiqueta SVG, atributos]. */
const ICONS = {
  home: [["path", { d: "M3 11 12 4l9 7" }], ["path", { d: "M5 10v10h14V10" }], ["path", { d: "M10 20v-6h4v6" }]],
  search: [["circle", { cx: 11, cy: 11, r: 7 }], ["line", { x1: 16.5, y1: 16.5, x2: 21, y2: 21 }]],
  book: [["path", { d: "M4 19V5a2 2 0 0 1 2-2h14v16H6a2 2 0 0 0-2 2 2 2 0 0 0 2 2h14" }], ["line", { x1: 9, y1: 7, x2: 16, y2: 7 }]],
  swords: [
    ["path", { d: "M4 4l10 10" }],
    ["path", { d: "M20 4 10 14" }],
    ["path", { d: "M5 14l5 5" }],
    ["path", { d: "M19 14l-5 5" }],
    ["path", { d: "M7.5 16.5 4 20" }],
    ["path", { d: "M16.5 16.5 20 20" }],
  ],
  flask: [["path", { d: "M9 3h6" }], ["path", { d: "M10 3v6l-5.4 9.4A1.7 1.7 0 0 0 6 21h12a1.7 1.7 0 0 0 1.4-2.6L14 9V3" }], ["path", { d: "M7.5 15h9" }]],
  gamepad: [
    ["rect", { x: 2, y: 7, width: 20, height: 11, rx: 5 }],
    ["line", { x1: 7, y1: 10.5, x2: 7, y2: 14.5 }],
    ["line", { x1: 5, y1: 12.5, x2: 9, y2: 12.5 }],
    ["circle", { cx: 15.5, cy: 11.5, r: 0.8, class: "icon__dot" }],
    ["circle", { cx: 18, cy: 13.8, r: 0.8, class: "icon__dot" }],
  ],
  chart: [["path", { d: "M4 20h16" }], ["rect", { x: 6, y: 11, width: 3, height: 6 }], ["rect", { x: 11, y: 6, width: 3, height: 11 }], ["rect", { x: 16, y: 13, width: 3, height: 4 }]],
  gear: gear(),
  user: [["circle", { cx: 12, cy: 8, r: 4 }], ["path", { d: "M4 21a8 8 0 0 1 16 0" }]],
  users: [["circle", { cx: 9, cy: 8, r: 3.5 }], ["path", { d: "M2.5 20a6.5 6.5 0 0 1 13 0" }], ["path", { d: "M15.5 4.6a3.5 3.5 0 0 1 0 6.8" }], ["path", { d: "M18 14.3a6.5 6.5 0 0 1 3.5 5.7" }]],
  link: [["path", { d: "M10 14a4.5 4.5 0 0 0 6.4 0l3-3a4.5 4.5 0 0 0-6.4-6.4l-1 1" }], ["path", { d: "M14 10a4.5 4.5 0 0 0-6.4 0l-3 3a4.5 4.5 0 0 0 6.4 6.4l1-1" }]],
  copy: [["rect", { x: 9, y: 9, width: 11, height: 11, rx: 2 }], ["path", { d: "M5 15H4a1 1 0 0 1-1-1V4a1 1 0 0 1 1-1h10a1 1 0 0 1 1 1v1" }]],
  logout: [["path", { d: "M14 4h5a1 1 0 0 1 1 1v14a1 1 0 0 1-1 1h-5" }], ["polyline", { points: "9 8 5 12 9 16" }], ["line", { x1: 5, y1: 12, x2: 15, y2: 12 }]],
  plus: [["line", { x1: 12, y1: 5, x2: 12, y2: 19 }], ["line", { x1: 5, y1: 12, x2: 19, y2: 12 }]],
  check: [["polyline", { points: "5 12 10 17 19 7" }]],
  "check-circle": [["circle", { cx: 12, cy: 12, r: 9 }], ["polyline", { points: "8 12 11 15 16 9" }]],
  star: [["polygon", { points: STAR }]],
  pokeball: [["circle", { cx: 12, cy: 12, r: 9 }], ["path", { d: "M3 12h6m6 0h6" }], ["circle", { cx: 12, cy: 12, r: 3 }]],
  download: [["line", { x1: 12, y1: 4, x2: 12, y2: 15 }], ["polyline", { points: "7 10 12 15 17 10" }], ["path", { d: "M5 20h14" }]],
  volume: [["polygon", { points: "4 9 8 9 13 5 13 19 8 15 4 15" }], ["path", { d: "M16.5 8.5a5 5 0 0 1 0 7" }], ["path", { d: "M19.5 5.5a9 9 0 0 1 0 13" }]],
  "volume-x": [["polygon", { points: "4 9 8 9 13 5 13 19 8 15 4 15" }], ["line", { x1: 17, y1: 9, x2: 22, y2: 14 }], ["line", { x1: 22, y1: 9, x2: 17, y2: 14 }]],
  music: [["path", { d: "M9 18V5l11-2v13" }], ["circle", { cx: 6, cy: 18, r: 3 }], ["circle", { cx: 17, cy: 16, r: 3 }]],
  sparkles: [["path", { d: "M11 3l1.8 5.2L18 10l-5.2 1.8L11 17l-1.8-5.2L4 10l5.2-1.8z" }], ["path", { d: "M19 14l.8 2.2L22 17l-2.2.8L19 20l-.8-2.2L16 17l2.2-.8z" }]],
  stop: [["rect", { x: 7, y: 7, width: 10, height: 10, rx: 1.5 }]],
  "chevron-left": [["polyline", { points: "15 6 9 12 15 18" }]],
  "chevron-right": [["polyline", { points: "9 6 15 12 9 18" }]],
  "arrow-left": [["line", { x1: 19, y1: 12, x2: 5, y2: 12 }], ["polyline", { points: "11 6 5 12 11 18" }]],
  "arrow-right": [["line", { x1: 5, y1: 12, x2: 19, y2: 12 }], ["polyline", { points: "13 6 19 12 13 18" }]],
  x: [["line", { x1: 6, y1: 6, x2: 18, y2: 18 }], ["line", { x1: 18, y1: 6, x2: 6, y2: 18 }]],
  help: [["circle", { cx: 12, cy: 12, r: 9 }], ["path", { d: "M9.6 9.2a2.5 2.5 0 1 1 3.4 2.3c-.7.3-1 .9-1 1.6v.4" }], ["circle", { cx: 12, cy: 16.8, r: 0.6, class: "icon__dot" }]],
  target: [["circle", { cx: 12, cy: 12, r: 9 }], ["circle", { cx: 12, cy: 12, r: 5 }], ["circle", { cx: 12, cy: 12, r: 1, class: "icon__dot" }]],
  trophy: [["path", { d: "M8 4h8v5a4 4 0 0 1-8 0z" }], ["path", { d: "M8 6H5a3 3 0 0 0 3.5 4" }], ["path", { d: "M16 6h3a3 3 0 0 1-3.5 4" }], ["path", { d: "M12 13v4" }], ["path", { d: "M8 20h8" }], ["path", { d: "M9.5 17h5" }]],
  flame: [["path", { d: "M12 3c.8 3.4 5 5.2 5 10a5 5 0 0 1-10 0c0-2.7 1.6-3.8 2-6.5 1.4 1 2.1 2.4 2.1 3.9.9-1 1.4-4 .9-7.4z" }]],
  shapes: [["circle", { cx: 7.5, cy: 7.5, r: 4 }], ["rect", { x: 13, y: 13, width: 8, height: 8, rx: 1 }], ["polygon", { points: "17 3 21 10 13 10" }], ["path", { d: "M4 20l4-6 4 6z" }]],
  bulb: [["path", { d: "M9 18h6" }], ["path", { d: "M10 21h4" }], ["path", { d: "M12 3a6 6 0 0 0-4 10.5c.7.7 1 1.5 1 2.5h6c0-1 .3-1.8 1-2.5A6 6 0 0 0 12 3z" }]],
  warning: [["path", { d: "M12 4 2.5 20h19z" }], ["line", { x1: 12, y1: 10, x2: 12, y2: 14 }], ["circle", { cx: 12, cy: 17, r: 0.6, class: "icon__dot" }]],
  shield: [["path", { d: "M12 3l8 3v6c0 5-3.5 8-8 9-4.5-1-8-4-8-9V6z" }], ["polyline", { points: "8.5 12 11 14.5 15.5 9.5" }]],
  refresh: [["path", { d: "M20 12a8 8 0 1 1-2.4-5.7" }], ["polyline", { points: "20 4 20 9 15 9" }]],
  tag: [["path", { d: "M3 12V4h8l10 10-8 8z" }], ["circle", { cx: 7.5, cy: 7.5, r: 1.3, class: "icon__dot" }]],
  message: [["path", { d: "M4 5h16v11H9l-5 4z" }], ["line", { x1: 8, y1: 9, x2: 16, y2: 9 }], ["line", { x1: 8, y1: 12.5, x2: 13, y2: 12.5 }]],
  rules: [["line", { x1: 9, y1: 6, x2: 20, y2: 6 }], ["line", { x1: 9, y1: 12, x2: 20, y2: 12 }], ["line", { x1: 9, y1: 18, x2: 20, y2: 18 }], ["polyline", { points: "3.5 6 4.5 7 6.5 5" }], ["polyline", { points: "3.5 12 4.5 13 6.5 11" }], ["polyline", { points: "3.5 18 4.5 19 6.5 17" }]],
  ban: [["circle", { cx: 12, cy: 12, r: 9 }], ["line", { x1: 5.6, y1: 5.6, x2: 18.4, y2: 18.4 }]],
  medal: [["circle", { cx: 12, cy: 15, r: 5 }], ["path", { d: "M8.5 3 12 10l3.5-7" }], ["polygon", { points: "12 12.6 12.8 14.2 14.5 14.4 13.2 15.6 13.6 17.3 12 16.4 10.4 17.3 10.8 15.6 9.5 14.4 11.2 14.2" }]],
  flag: [["path", { d: "M5 21V4" }], ["path", { d: "M5 4h11l-2 4 2 4H5" }]],
  bolt: [["polygon", { points: "13 2 4 14 11 14 10 22 20 9 13 9" }]],
};

/**
 * Icono SVG. `fill` lo rellena (estrella de favorito, Poké Ball de capturado).
 * `label` lo hace legible para lectores de pantalla cuando va solo, sin texto al lado.
 */
export function icon(name, { fill = false, label = null, size = null } = {}) {
  const shapes = ICONS[name] ?? ICONS.help;
  const node = svg(
    "svg",
    {
      class: `icon${fill ? " icon--fill" : ""}`,
      viewBox: "0 0 24 24",
      width: size,
      height: size,
      "aria-hidden": label ? null : "true",
      role: label ? "img" : null,
      "aria-label": label,
      focusable: "false",
    },
    shapes.map(([tag, attrs]) => svg(tag, attrs)),
  );
  return node;
}

export const ICON_NAMES = Object.freeze(Object.keys(ICONS));
