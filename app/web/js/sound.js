// Sonido de la Pokédex.
//
// - Gritos de los Pokémon (repositorio de PokeAPI) con fundido de entrada y de salida:
//   nunca empiezan ni se cortan de golpe.
// - Efectos sintetizados en el navegador con Web Audio (osciladores con envolvente):
//   sin ficheros, sin melodías ajenas y con su propio fundido.
// - Volumen general y silencio, recordados en este navegador.
// Los navegadores solo dejan sonar audio tras una acción del usuario: todo se dispara
// desde clics, nunca al cargar la página.

import { prefs } from "./dom.js";

const CRIES = "https://raw.githubusercontent.com/PokeAPI/cries/main/cries/pokemon/latest";
const FADE_IN_MS = 120;
const FADE_OUT_MS = 450;

const listeners = new Set();
let muted = prefs.get("muted", false);
let context = null;
let master = null;
let currentCry = null;

export function cryUrl(id) {
  return `${CRIES}/${id}.ogg`;
}

export function isMuted() {
  return muted;
}

export function setMuted(value) {
  muted = Boolean(value);
  prefs.set("muted", muted);
  if (muted) stopCry({ fade: false });
  if (master) master.gain.value = muted ? 0 : 1;
  for (const listener of listeners) listener(muted);
}

export function onMuteChange(listener) {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

function audio() {
  if (!context) {
    const Ctx = window.AudioContext ?? window.webkitAudioContext;
    if (!Ctx) return null;
    context = new Ctx();
    master = context.createGain();
    master.gain.value = muted ? 0 : 1;
    master.connect(context.destination);
  }
  if (context.state === "suspended") context.resume();
  return context;
}

// ── Gritos con fundido ───────────────────────────────────────
/**
 * Lleva el volumen de un <audio> de `from` a `to` en `ms`. Usa un temporizador y no
 * requestAnimationFrame, que se detiene con la pestaña en segundo plano.
 */
function fade(element, from, to, ms) {
  return new Promise((resolve) => {
    const start = performance.now();
    element.volume = from;
    const timer = setInterval(() => {
      const t = Math.min(1, (performance.now() - start) / ms);
      element.volume = Math.max(0, Math.min(1, from + (to - from) * t));
      if (t >= 1) {
        clearInterval(timer);
        resolve();
      }
    }, 16);
  });
}

export function stopCry({ fade: smooth = true } = {}) {
  const cry = currentCry;
  currentCry = null;
  if (!cry) return;
  if (!smooth) {
    cry.pause();
    return;
  }
  fade(cry, cry.volume, 0, FADE_OUT_MS).then(() => cry.pause());
}

/**
 * Reproduce el grito con fundido de entrada y lo apaga con fundido antes de acabar.
 * Devuelve false si el navegador no puede (Safari no lee OGG) o si está en silencio.
 */
export async function playCry(id, { volume = 0.55 } = {}) {
  if (muted) return true;
  stopCry();
  const cry = new Audio(cryUrl(id));
  cry.volume = 0;
  currentCry = cry;
  cry.addEventListener("timeupdate", () => {
    // Fundido de salida al acercarse al final
    if (cry === currentCry && cry.duration && cry.duration - cry.currentTime < FADE_OUT_MS / 1000) {
      currentCry = null;
      fade(cry, cry.volume, 0, Math.max(60, (cry.duration - cry.currentTime) * 1000));
    }
  });
  try {
    await cry.play();
    fade(cry, 0, volume, FADE_IN_MS); // sin esperar: el grito ya está sonando
    return true;
  } catch {
    if (currentCry === cry) currentCry = null;
    return false;
  }
}

// ── Efectos sintetizados ─────────────────────────────────────
/**
 * Una nota con envolvente: sube en `attack` segundos y se apaga suavemente (fundido) en
 * `release`. `glide` desliza la frecuencia hasta ese valor.
 */
function tone(ctx, { freq, start = 0, duration = 0.12, type = "triangle", gain = 0.18, attack = 0.012, release = 0.12, glide = null }) {
  const t0 = ctx.currentTime + start;
  const osc = ctx.createOscillator();
  const env = ctx.createGain();
  osc.type = type;
  osc.frequency.setValueAtTime(freq, t0);
  if (glide) osc.frequency.exponentialRampToValueAtTime(glide, t0 + duration);
  env.gain.setValueAtTime(0.0001, t0);
  env.gain.exponentialRampToValueAtTime(gain, t0 + attack);
  env.gain.setValueAtTime(gain, t0 + duration);
  env.gain.exponentialRampToValueAtTime(0.0001, t0 + duration + release);
  osc.connect(env).connect(master);
  osc.start(t0);
  osc.stop(t0 + duration + release + 0.02);
}

/** Ruido blanco filtrado con fundido: golpes. */
function noise(ctx, { start = 0, duration = 0.12, gain = 0.25, frequency = 1200 }) {
  const t0 = ctx.currentTime + start;
  const buffer = ctx.createBuffer(1, Math.ceil(ctx.sampleRate * (duration + 0.05)), ctx.sampleRate);
  const data = buffer.getChannelData(0);
  for (let i = 0; i < data.length; i += 1) data[i] = Math.random() * 2 - 1;
  const source = ctx.createBufferSource();
  source.buffer = buffer;
  const filter = ctx.createBiquadFilter();
  filter.type = "lowpass";
  filter.frequency.value = frequency;
  const env = ctx.createGain();
  env.gain.setValueAtTime(gain, t0);
  env.gain.exponentialRampToValueAtTime(0.0001, t0 + duration);
  source.connect(filter).connect(env).connect(master);
  source.start(t0);
  source.stop(t0 + duration + 0.05);
}

// Notas (Hz)
const N = { C5: 523.25, D5: 587.33, E5: 659.25, F5: 698.46, G5: 783.99, A5: 880, B5: 987.77, C6: 1046.5, E6: 1318.5, G4: 392, E4: 329.63, C4: 261.63 };

const EFFECTS = {
  /** Pulsar un botón importante */
  click: (ctx) => tone(ctx, { freq: 1400, duration: 0.02, type: "square", gain: 0.04, release: 0.04 }),
  /** Añadir a la Pokédex o capturar */
  capture: (ctx) => {
    [N.C5, N.E5, N.G5, N.C6].forEach((freq, i) => tone(ctx, { freq, start: i * 0.07, duration: 0.07 }));
  },
  /** Acierto */
  success: (ctx) => {
    tone(ctx, { freq: N.E5, duration: 0.09 });
    tone(ctx, { freq: N.B5, start: 0.1, duration: 0.18, release: 0.25 });
  },
  /** Fallo */
  fail: (ctx) => tone(ctx, { freq: N.G4, duration: 0.25, type: "sawtooth", gain: 0.08, glide: N.C4, release: 0.2 }),
  /** Misión cumplida */
  mission: (ctx) => {
    [N.G5, N.C6, N.E6].forEach((freq, i) => tone(ctx, { freq, start: i * 0.09, duration: 0.09 }));
    tone(ctx, { freq: N.C6, start: 0.3, duration: 0.25, release: 0.4, gain: 0.14 });
  },
  /** Golpe normal en combate */
  hit: (ctx) => {
    noise(ctx, { duration: 0.1, gain: 0.22, frequency: 1600 });
    tone(ctx, { freq: 180, duration: 0.06, type: "square", gain: 0.08, glide: 90 });
  },
  /** Golpe supereficaz */
  super: (ctx) => {
    noise(ctx, { duration: 0.16, gain: 0.3, frequency: 3000 });
    tone(ctx, { freq: 300, duration: 0.1, type: "square", gain: 0.1, glide: 120 });
    tone(ctx, { freq: N.E6, start: 0.05, duration: 0.05, gain: 0.06 });
  },
  /** Golpe poco eficaz */
  weak: (ctx) => noise(ctx, { duration: 0.08, gain: 0.12, frequency: 700 }),
  /** Un Pokémon se debilita */
  faint: (ctx) => tone(ctx, { freq: N.A5, duration: 0.5, type: "triangle", gain: 0.12, glide: 110, release: 0.3 }),
  /** Subida de nivel */
  levelup: (ctx) => {
    [N.C5, N.D5, N.E5, N.G5, N.C6].forEach((freq, i) => tone(ctx, { freq, start: i * 0.06, duration: 0.06, type: "square", gain: 0.07 }));
  },
  /** Victoria */
  victory: (ctx) => {
    [N.C5, N.E5, N.G5].forEach((freq, i) => tone(ctx, { freq, start: i * 0.12, duration: 0.11, type: "square", gain: 0.08 }));
    tone(ctx, { freq: N.C6, start: 0.38, duration: 0.45, type: "square", gain: 0.09, release: 0.5 });
    tone(ctx, { freq: N.E5, start: 0.38, duration: 0.45, gain: 0.08, release: 0.5 });
  },
  /** Derrota */
  defeat: (ctx) => {
    [N.G4, N.E4, N.C4].forEach((freq, i) => tone(ctx, { freq, start: i * 0.18, duration: 0.17, type: "triangle", gain: 0.12, release: 0.3 }));
  },
};

export const EFFECT_NAMES = Object.freeze(Object.keys(EFFECTS));

/** Reproduce un efecto («capture», «hit», «victory»…). Nunca lanza errores. */
export function sfx(name) {
  if (muted || !Object.hasOwn(EFFECTS, name)) return;
  try {
    const ctx = audio();
    if (ctx) EFFECTS[name](ctx);
  } catch {
    /* sin Web Audio: la web funciona igual, en silencio */
  }
}

// Al cambiar de pantalla, el grito se apaga con fundido en vez de cortarse.
window.addEventListener("hashchange", () => stopCry());
