// Construcción del DOM sin innerHTML: todo el texto entra con textContent, así que un
// nombre, un apodo o una nota con «<script>» se muestran tal cual y nunca se ejecutan.

const SVG_NS = "http://www.w3.org/2000/svg";

function applyProps(node, props) {
  for (const [key, value] of Object.entries(props ?? {})) {
    if (value === null || value === undefined || value === false) continue;
    if (key === "class") node.setAttribute("class", value);
    else if (key === "text") node.textContent = value;
    else if (key === "dataset") Object.assign(node.dataset, value);
    else if (key === "style") {
      // CSSOM: permitido por la CSP, a diferencia del atributo style.
      for (const [prop, v] of Object.entries(value)) node.style.setProperty(prop, v);
    } else if (key.startsWith("on") && typeof value === "function") {
      node.addEventListener(key.slice(2).toLowerCase(), value);
    } else if (value === true) node.setAttribute(key, "");
    else node.setAttribute(key, String(value));
  }
}

export function append(node, children) {
  for (const child of children.flat(Infinity)) {
    if (child === null || child === undefined || child === false) continue;
    node.append(child instanceof Node ? child : String(child));
  }
  return node;
}

/** el("button", {class: "btn", onClick: fn}, "Texto", otroNodo) */
export function el(tag, props = {}, ...children) {
  const node = document.createElement(tag);
  applyProps(node, props);
  return append(node, children);
}

export function svg(tag, props = {}, ...children) {
  const node = document.createElementNS(SVG_NS, tag);
  applyProps(node, props);
  return append(node, children);
}

export function clear(node) {
  node.replaceChildren();
  return node;
}

/** Espera `ms` milisegundos sin bloquear. */
export function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

/** Llama a `fn` cuando el usuario deja de escribir durante `ms`. */
export function debounce(fn, ms = 300) {
  let timer = null;
  return (...args) => {
    clearTimeout(timer);
    timer = setTimeout(() => fn(...args), ms);
  };
}

/** Lectura y escritura en localStorage que nunca rompe la página (modo privado, etc.). */
export const prefs = {
  get(key, fallback = null) {
    try {
      const value = window.localStorage.getItem(`pokedex.${key}`);
      return value === null ? fallback : JSON.parse(value);
    } catch {
      return fallback;
    }
  },
  set(key, value) {
    try {
      window.localStorage.setItem(`pokedex.${key}`, JSON.stringify(value));
    } catch {
      /* sin almacenamiento: la preferencia dura solo esta visita */
    }
  },
};
