// Arranque de la web de la Pokédex.

import { events, isLoggedIn, logout, restoreSession, takeTokensFromUrl } from "./api.js";
import { el } from "./dom.js";
import { currentPath, match, navRoutes, navigate, route } from "./router.js";
import { loadUser, session } from "./session.js";
import { initTechPanel } from "./techpanel.js";
import { showError, toast } from "./ui.js";
import { onCollectionChange } from "./data.js";
import { watchMissions } from "./missions.js";
import { isMuted, onMuteChange, setMuted, sfx } from "./sound.js";
import { renderAuth } from "./views/auth.js";
import { renderHome } from "./views/home.js";
import { registerScreens } from "./screens.js";
import { icon } from "./icons.js";
import { initOakPanel } from "./oak-chat.js";
import { startTour } from "./tour.js";

const view = document.getElementById("view");
const nav = document.getElementById("nav");
const userMenu = document.getElementById("user-menu");

// ── Sonido ───────────────────────────────────────────────────
function initSound() {
  const button = document.getElementById("sound-toggle");
  const draw = (muted) => {
    button.setAttribute("aria-pressed", String(muted));
    button.replaceChildren(icon(muted ? "volume-x" : "volume"), el("span", { class: "visually-hidden" }, "Silenciar el sonido"));
    button.title = muted ? "Activar el sonido" : "Silenciar el sonido";
  };
  button.addEventListener("click", () => setMuted(!isMuted()));
  onMuteChange(draw);
  draw(isMuted());
}

// ── Pantallas ────────────────────────────────────────────────
route("/", { title: "Inicio", render: renderHome, nav: { label: "Inicio", icon: "home", order: 0, primary: true } });
registerScreens();

// Menú con lo esencial a la vista (Inicio, Mi Pokédex, Equipos, Combate) y el resto en
// «Más», para que quien empieza no vea ocho botones a la vez.
function navLink(r, active, extraClass = "") {
  return el(
    "a",
    { class: `nav__link ${extraClass}`.trim(), href: `#${r.pattern}`, "aria-current": r.pattern === active ? "page" : null },
    icon(r.nav.icon),
    r.nav.label,
  );
}

// Un clic fuera cierra «Más» (una sola escucha: el menú se repinta en cada pantalla)
document.addEventListener("click", (event) => {
  const menu = document.getElementById("nav-menu");
  if (menu && !menu.hidden && !menu.contains(event.target)) {
    menu.hidden = true;
    document.getElementById("nav-more")?.setAttribute("aria-expanded", "false");
  }
});

function renderNav(active) {
  const routes = navRoutes();
  const extra = routes.filter((r) => !r.nav.primary);
  const inMore = extra.some((r) => r.pattern === active);
  const toggle = el(
    "button",
    { type: "button", class: "nav__link nav__more", id: "nav-more", "aria-expanded": "false", "aria-controls": "nav-menu", "aria-current": inMore ? "page" : null },
    icon("plus"),
    "Más",
  );
  const help = el("button", { type: "button", class: "nav__menu-item" }, icon("help"), "¿Cómo funciona?");
  const menu = el(
    "div",
    { class: "nav__menu", id: "nav-menu", hidden: true },
    extra.map((r) => el("a", { class: "nav__menu-item", href: `#${r.pattern}`, "aria-current": r.pattern === active ? "page" : null }, icon(r.nav.icon), el("span", {}, el("strong", {}, r.nav.label), el("small", {}, r.nav.hint ?? "")))),
    help,
  );
  const setOpen = (open) => {
    menu.hidden = !open;
    toggle.setAttribute("aria-expanded", String(open));
    if (open) {
      // Fijo bajo el botón: la barra del menú se desplaza en horizontal y recortaría un
      // desplegable normal
      const box = toggle.getBoundingClientRect();
      const width = Math.min(320, window.innerWidth - 32);
      menu.style.setProperty("top", `${box.bottom + 8}px`);
      menu.style.setProperty("left", `${Math.max(16, Math.min(box.left, window.innerWidth - width - 16))}px`);
      menu.style.setProperty("width", `${width}px`);
      menu.querySelector("a, button")?.focus();
    }
  };
  toggle.addEventListener("click", (event) => {
    event.stopPropagation();
    setOpen(menu.hidden);
  });
  help.addEventListener("click", () => {
    setOpen(false);
    startTour();
  });
  menu.addEventListener("keydown", (event) => {
    if (event.key === "Escape") {
      setOpen(false);
      toggle.focus();
    }
  });
  nav.replaceChildren(...routes.filter((r) => r.nav.primary).map((r) => navLink(r, active)), el("div", { class: "nav__more-wrap" }, toggle, menu));
}

function renderUserMenu() {
  if (!session.user) {
    userMenu.hidden = true;
    return;
  }
  userMenu.hidden = false;
  userMenu.replaceChildren(
    el("span", { class: "user-chip__name" }, icon("user"), session.user.username),
    el(
      "button",
      {
        type: "button",
        class: "chip-toggle",
        onClick: async () => {
          await logout().catch(() => {});
          toast("Has salido. ¡Hasta pronto!", { type: "ok" });
        },
      },
      icon("logout"),
      "Salir",
    ),
  );
}

let renderToken = 0;
let syncOak = () => {};

async function render() {
  const token = ++renderToken;
  syncOak();
  if (!isLoggedIn()) {
    nav.hidden = true;
    renderUserMenu();
    document.title = "Entrar · Pokédex";
    renderAuth(view, {
      onSuccess: async ({ isNew } = {}) => {
        await loadUser();
        toast(isNew ? `¡Bienvenido, ${session.user.username}! Tu aventura empieza aquí.` : `¡Hola de nuevo, ${session.user.username}!`, { type: "ok" });
        session.isNew = Boolean(isNew);
        renderUserMenu();
        navigate(isNew ? "/" : currentPath());
      },
    });
    return;
  }

  const path = currentPath();
  const found = match(path) ?? match("/");
  nav.hidden = false;
  // Una ficha pertenece a «Buscar»: así el menú indica dónde estás.
  renderNav(found.route.parent ?? found.route.pattern);
  renderUserMenu();
  document.title = `${found.route.title} · Pokédex`;
  try {
    await found.route.render(view, found.params, { isCurrent: () => token === renderToken });
  } catch (error) {
    if (token === renderToken) showError(error);
  }
  if (token === renderToken) {
    // Cada pantalla empieza arriba, y el lector de pantalla anuncia el contenido nuevo.
    view.scrollTo({ top: 0 }); // la página es fija: lo que se desplaza es la pantalla
    view.focus({ preventScroll: true });
  }
}

events.addEventListener("session", (event) => {
  if (!event.detail.loggedIn) {
    session.user = null;
    render();
  }
});

async function start() {
  initTechPanel();
  watchMissions(onCollectionChange, (message) => {
    sfx("mission");
    toast([icon("medal"), message], { type: "ok", timeout: 6000 });
  });
  initSound();
  syncOak = initOakPanel();
  const fromGoogle = takeTokensFromUrl();
  if (fromGoogle || (await restoreSession())) {
    try {
      await loadUser();
      if (fromGoogle) toast(`¡Hola, ${session.user.username}! Has entrado con Google.`, { type: "ok" });
    } catch {
      /* la sesión no era válida: se muestra la pantalla de acceso */
    }
  }
  window.addEventListener("hashchange", render);
  render();
}

start();
