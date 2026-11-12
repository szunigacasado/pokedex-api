// Pantalla de acceso: entrar o crear cuenta, con la política de contraseñas en vivo y
// el botón de Google solo si el servidor lo tiene configurado.

import { ApiError, login, request } from "../api.js";
import { el } from "../dom.js";
import { passwordChecks, passwordIsValid, usernameProblem } from "../format.js";
import { busy } from "../ui.js";

function welcome() {
  return el(
    "section",
    { class: "auth__welcome", "aria-labelledby": "welcome-title" },
    el("h1", { id: "welcome-title" }, "¡Hola, entrenador!"),
    el("p", {}, "Esta es tu Pokédex personal. Así funciona:"),
    el(
      "ol",
      { class: "steps" },
      el("li", {}, el("div", {}, el("strong", {}, "Crea tu cuenta"), el("br"), "Solo un nombre, un email y una contraseña.")),
      el("li", {}, el("div", {}, el("strong", {}, "Busca y captura"), el("br"), "Encuentra Pokémon y guárdalos en tu colección.")),
      el("li", {}, el("div", {}, el("strong", {}, "Arma tu equipo"), el("br"), "Junta hasta seis y descubre sus puntos fuertes y débiles.")),
      el("li", {}, el("div", {}, el("strong", {}, "Pide consejo al Profesor Oak"), el("br"), "Te dirá cómo mejorar tu equipo.")),
    ),
  );
}

function errorBox() {
  return el("div", { class: "alert", role: "alert", hidden: true, tabindex: "-1" });
}

function showProblem(box, message) {
  box.textContent = message;
  box.hidden = !message;
  if (message) box.focus();
}

function passwordField(id, labelText, autocomplete) {
  const input = el("input", { class: "input", id, name: "password", type: "password", required: true, autocomplete, "aria-describedby": `${id}-hint` });
  const toggle = el(
    "button",
    {
      type: "button",
      class: "btn btn--secondary",
      "aria-pressed": "false",
      "aria-label": "Mostrar la contraseña",
      onClick: () => {
        const show = input.type === "password";
        input.type = show ? "text" : "password";
        toggle.setAttribute("aria-pressed", String(show));
        toggle.textContent = show ? "Ocultar" : "Ver";
      },
    },
    "Ver",
  );
  const field = el("div", { class: "field" }, el("label", { for: id }, labelText), el("div", { class: "input-row" }, input, toggle));
  return { field, input };
}

function loginForm(onSuccess) {
  const problem = errorBox();
  const user = el("input", { class: "input", id: "login-user", name: "username", required: true, autocomplete: "username" });
  const { field: passField, input: pass } = passwordField("login-pass", "Contraseña", "current-password");
  const submit = el("button", { class: "btn btn--primary btn--block", type: "submit" }, "Entrar");

  const form = el(
    "form",
    { novalidate: true, "aria-label": "Entrar" },
    problem,
    el("div", { class: "field" }, el("label", { for: "login-user" }, "Usuario o email"), user),
    passField,
    el("p", { class: "field__hint", id: "login-pass-hint" }, "Si fallas 5 veces seguidas, la cuenta se bloquea 15 minutos por seguridad."),
    submit,
  );
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    if (!user.value.trim() || !pass.value) {
      showProblem(problem, "Escribe tu usuario (o email) y tu contraseña.");
      return;
    }
    try {
      await busy(submit, () => login(user.value.trim(), pass.value), "Entrando…");
      onSuccess();
    } catch (error) {
      showProblem(problem, error instanceof ApiError ? error.message : "No se ha podido entrar.");
    }
  });
  return form;
}

function registerForm(onSuccess) {
  const problem = errorBox();
  const user = el("input", { class: "input", id: "reg-user", name: "username", required: true, autocomplete: "username", "aria-describedby": "reg-user-hint" });
  const email = el("input", { class: "input", id: "reg-email", name: "email", type: "email", required: true, autocomplete: "email" });
  const { field: passField, input: pass } = passwordField("reg-pass", "Contraseña", "new-password");
  const checklist = el("ul", { class: "checklist", id: "reg-pass-hint", "aria-live": "polite" });
  const submit = el("button", { class: "btn btn--primary btn--block", type: "submit" }, "Crear cuenta y entrar");

  const renderChecks = () => {
    checklist.replaceChildren(
      ...passwordChecks(pass.value, user.value).map((check) =>
        el("li", { class: check.ok ? "is-ok" : null }, check.label, el("span", { class: "visually-hidden" }, check.ok ? " (cumplido)" : " (pendiente)")),
      ),
    );
  };
  pass.addEventListener("input", renderChecks);
  user.addEventListener("input", renderChecks);
  renderChecks();

  const form = el(
    "form",
    { novalidate: true, "aria-label": "Crear cuenta" },
    problem,
    el(
      "div",
      { class: "field" },
      el("label", { for: "reg-user" }, "Nombre de entrenador"),
      user,
      el("span", { class: "field__hint", id: "reg-user-hint" }, "Entre 3 y 50 caracteres: letras sin acentos, números, punto o guiones."),
    ),
    el("div", { class: "field" }, el("label", { for: "reg-email" }, "Email"), email),
    passField,
    checklist,
    el("div", { class: "field" }),
    submit,
  );

  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    const nameProblem = usernameProblem(user.value);
    if (nameProblem) return showProblem(problem, `Nombre de entrenador: ${nameProblem}`);
    if (!email.value.includes("@")) return showProblem(problem, "Escribe un email válido.");
    if (!passwordIsValid(pass.value, user.value)) return showProblem(problem, "La contraseña aún no cumple todos los requisitos de la lista.");
    try {
      await busy(
        submit,
        async () => {
          await request("/api/v1/auth/register", {
            method: "POST",
            json: { username: user.value.trim(), email: email.value.trim(), password: pass.value },
            auth: false,
          });
          await login(user.value.trim(), pass.value);
        },
        "Creando tu cuenta…",
      );
      onSuccess({ isNew: true });
    } catch (error) {
      showProblem(problem, error instanceof ApiError ? error.message : "No se ha podido crear la cuenta.");
    }
  });
  return form;
}

async function googleButton() {
  try {
    const { enabled } = await request("/api/v1/auth/google/status", { auth: false });
    if (!enabled) return null;
  } catch {
    return null;
  }
  return el(
    "div",
    {},
    el("div", { class: "divider" }, "o también"),
    el(
      "a",
      { class: "btn btn--google btn--block", href: "/api/v1/auth/google/login" },
      el("span", { "aria-hidden": "true" }, "G"),
      "Entrar con Google",
    ),
  );
}

export function renderAuth(view, { onSuccess }) {
  let mode = "login";
  const formSlot = el("div", {});
  const tabs = {
    login: el("button", { type: "button", "aria-pressed": "true" }, "Ya tengo cuenta"),
    register: el("button", { type: "button", "aria-pressed": "false" }, "Soy nuevo"),
  };
  const googleSlot = el("div", {});

  const show = (next) => {
    mode = next;
    for (const [key, button] of Object.entries(tabs)) button.setAttribute("aria-pressed", String(key === mode));
    formSlot.replaceChildren(mode === "login" ? loginForm(onSuccess) : registerForm(onSuccess));
    formSlot.querySelector("input")?.focus();
  };
  tabs.login.addEventListener("click", () => show("login"));
  tabs.register.addEventListener("click", () => show("register"));

  view.replaceChildren(
    el(
      "div",
      { class: "auth" },
      welcome(),
      el(
        "section",
        { class: "panel", "aria-label": "Acceso" },
        el("div", { class: "segmented", role: "group", "aria-label": "Elige una opción" }, tabs.login, tabs.register),
        formSlot,
        googleSlot,
        el("p", { class: "legal-link muted" }, el("a", { href: "/privacidad" }, "Política de privacidad")),
      ),
    ),
  );
  show("login");
  googleButton().then((node) => node && googleSlot.replaceChildren(node));
}
