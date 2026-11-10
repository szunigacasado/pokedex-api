// Enrutado por el fragmento de la URL (#/buscar, #/pokemon/25…). El fragmento nunca se
// envía al servidor, así que la API solo tiene que servir «/».

const routes = [];

/**
 * Registra una pantalla.
 * @param {string} pattern  "/pokemon/:id"
 * @param {object} screen   { title, render(params), nav?: {label, icon, order} }
 */
export function route(pattern, screen) {
  const keys = [];
  const regex = new RegExp(
    `^${pattern.replace(/\/:([a-z]+)/gi, (_, key) => {
      keys.push(key);
      return "/([^/]+)";
    })}/?$`,
  );
  routes.push({ pattern, regex, keys, ...screen });
}

export function navRoutes() {
  return routes.filter((r) => r.nav).sort((a, b) => a.nav.order - b.nav.order);
}

export function currentPath() {
  const hash = window.location.hash.replace(/^#/, "");
  return hash.startsWith("/") ? hash : "/";
}

export function match(path) {
  for (const r of routes) {
    const found = r.regex.exec(path);
    if (found) {
      const params = Object.fromEntries(r.keys.map((key, i) => [key, decodeURIComponent(found[i + 1])]));
      return { route: r, params };
    }
  }
  return null;
}

export function navigate(path) {
  if (currentPath() === path) window.dispatchEvent(new HashChangeEvent("hashchange"));
  else window.location.hash = `#${path}`;
}
