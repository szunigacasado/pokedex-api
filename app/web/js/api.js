// Cliente de la API REST. Es la única pieza de la web que conoce los tokens.
//
// - El access token vive SOLO en memoria: un script ajeno no puede leerlo de ningún
//   almacenamiento, y al recargar se pide uno nuevo.
// - El refresh token NO lo ve la web: vive en una cookie HttpOnly que solo se envía a
//   /api/v1/auth (ver app/session_cookie.py). Por eso entrar desde /docs inicia también la
//   sesión aquí, y una recarga no la cierra. Cada uso la rota.
// - Ante un 401 se renueva la sesión una vez y se repite la petición, sin que el usuario
//   lo note. Si no se puede, se avisa con el evento "session".
// - Cada petición se anuncia con el evento "request" para el modo técnico.

import { filenameFromDisposition, friendlyError } from "./format.js";

export const events = new EventTarget();

// Las versiones anteriores guardaban el refresh token en sessionStorage: se borra el resto.
try {
  window.sessionStorage?.removeItem("pokedex.refresh");
} catch {
  /* sin almacenamiento: nada que borrar */
}

let accessToken = null;
let expiresAt = 0;
let refreshing = null;
let sequence = 0;

export class ApiError extends Error {
  constructor(status, message, { retryAfter = null, detail = null } = {}) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.retryAfter = retryAfter;
    this.detail = detail;
  }
}

export function isLoggedIn() {
  return accessToken !== null;
}

/** Segundos que le quedan al access token (para el modo técnico). */
export function accessTokenSecondsLeft() {
  return accessToken ? Math.max(0, Math.round((expiresAt - Date.now()) / 1000)) : 0;
}

export function setSession(pair) {
  accessToken = pair.access_token;
  expiresAt = Date.now() + Number(pair.expires_in ?? 0) * 1000;
  events.dispatchEvent(new CustomEvent("session", { detail: { loggedIn: true } }));
}

export function clearSession() {
  const wasLoggedIn = accessToken !== null;
  accessToken = null;
  expiresAt = 0;
  if (wasLoggedIn) events.dispatchEvent(new CustomEvent("session", { detail: { loggedIn: false } }));
}

/**
 * Access token vigente para abrir un WebSocket (que no pasa por request()): si está a
 * punto de caducar, se renueva antes con la cookie. Nunca se guarda ni se pone en una URL.
 */
export async function socketToken() {
  if (accessTokenSecondsLeft() < 30) await refreshSession();
  return accessToken;
}

/** Recupera la sesión con la cookie: tras una recarga, o si se entró desde /docs. */
export async function restoreSession() {
  try {
    await refreshSession();
    return true;
  } catch {
    clearSession();
    return false;
  }
}

/**
 * Una sola renovación a la vez: en esta pestaña (las peticiones con 401 esperan a la misma)
 * y entre pestañas (Web Locks). Presentar dos veces el mismo refresh token se trataría
 * como un robo y cerraría la sesión en todas partes.
 */
function withRefreshLock(task) {
  return navigator.locks?.request ? navigator.locks.request("pokedex-refresh", task) : task();
}

function refreshSession() {
  refreshing ??= withRefreshLock(async () => {
    const pair = await request("/api/v1/auth/refresh", { method: "POST", auth: false });
    setSession(pair);
  }).finally(() => {
    refreshing = null;
  });
  return refreshing;
}

function announce(entry) {
  events.dispatchEvent(new CustomEvent("request", { detail: entry }));
}

/**
 * Llama a la API.
 * @param {string} path  Ruta, por ejemplo "/api/v1/pokedex".
 * @param {object} options  method, json, form, query, auth, as ("json" | "blob"), signal.
 */
export async function request(path, options = {}) {
  const { method = "GET", json, form, query, auth = true, as = "json", signal, retried = false } = options;
  const url = new URL(path, window.location.origin);
  for (const [key, value] of Object.entries(query ?? {})) {
    if (value !== undefined && value !== null && value !== "") url.searchParams.set(key, value);
  }

  const headers = {};
  let body;
  if (json !== undefined) {
    headers["Content-Type"] = "application/json";
    body = JSON.stringify(json);
  } else if (form !== undefined) {
    body = new URLSearchParams(form);
  }
  if (auth && accessToken) headers.Authorization = `Bearer ${accessToken}`;

  const id = ++sequence;
  const started = performance.now();
  const shownPath = url.pathname + url.search;
  let response;
  try {
    response = await fetch(url, { method, headers, body, signal, cache: "no-store", credentials: "same-origin" });
  } catch (error) {
    if (error?.name === "AbortError") throw error;
    announce({ id, method, path: shownPath, status: 0, ms: Math.round(performance.now() - started) });
    throw new ApiError(0, friendlyError(0));
  }

  announce({
    id,
    method,
    path: shownPath,
    status: response.status,
    ms: Math.round(performance.now() - started),
    requestId: response.headers.get("x-request-id"),
    remaining: response.headers.get("x-ratelimit-remaining"),
    limit: response.headers.get("x-ratelimit-limit"),
    renewed: retried,
  });

  if (response.status === 401 && auth && !retried) {
    try {
      await refreshSession();
    } catch {
      clearSession();
      throw new ApiError(401, friendlyError(401));
    }
    return request(path, { ...options, retried: true });
  }

  if (!response.ok) {
    let data = null;
    try {
      data = await response.json();
    } catch {
      /* respuesta sin JSON */
    }
    const retryAfter = response.headers.get("retry-after");
    if (response.status === 401 && auth) clearSession();
    throw new ApiError(response.status, friendlyError(response.status, data, retryAfter), {
      retryAfter: retryAfter === null ? null : Number(retryAfter),
      detail: data?.detail ?? null,
    });
  }

  if (response.status === 204) return null;
  if (as === "blob") {
    return {
      blob: await response.blob(),
      filename: filenameFromDisposition(response.headers.get("content-disposition")),
    };
  }
  return response.json();
}

/** Descarga un fichero de la API (CSV, PDF, PNG) con el nombre que propone el servidor. */
export async function download(path, query) {
  const { blob, filename } = await request(path, { query, as: "blob" });
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = filename;
  document.body.append(link);
  link.click();
  link.remove();
  setTimeout(() => URL.revokeObjectURL(url), 10_000);
  return filename;
}

export async function login(username, password) {
  const pair = await request("/api/v1/auth/login", { method: "POST", form: { username, password }, auth: false });
  setSession(pair);
}

export async function logout() {
  try {
    await request("/api/v1/auth/logout", { method: "POST", auth: false });
  } finally {
    clearSession();
  }
}

/**
 * Tras volver de Google, la API redirige a «/#access_token=…». Se toma el access token y se
 * borra todo de la barra de direcciones y del historial; el refresh token ya está en la
 * cookie HttpOnly, así que el del fragmento se descarta.
 */
export function takeTokensFromUrl() {
  const hash = window.location.hash;
  if (!hash.startsWith("#access_token=")) return false;
  const params = new URLSearchParams(hash.slice(1));
  window.history.replaceState(null, "", `${window.location.pathname}#/`);
  if (!params.get("access_token")) return false;
  setSession({ access_token: params.get("access_token"), expires_in: params.get("expires_in") });
  return true;
}
