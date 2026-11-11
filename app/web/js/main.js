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
route("/", { title: "Inicio", render: renderHome, nav: { label: "Inicio", icon: "home", order: 0 } });
registerScreens();

function renderNav(active) {
  nav.replaceChildren(
    ...navRoutes().map((r) =>
      el(
        "a",
        { class: "nav__link", href: `#${r.pattern}`, "aria-current": r.pattern === active ? "page" : null },
        icon(r.nav.icon),
        r.nav.label,
      ),
    ),
  );
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

async function render() {
  const token = ++renderToken;
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
    window.scrollTo({ top: 0 });
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
