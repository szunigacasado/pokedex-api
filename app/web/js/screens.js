// Registro de las pantallas de la web. Cada sección se añade aquí con route().

import { route } from "./router.js";
import { renderCollection } from "./views/collection.js";
import { renderBattle, renderBattleLobby } from "./views/battle.js";
import { voiceButtons } from "./views/extras.js";
import { renderGame } from "./views/game.js";
import { renderOak } from "./views/oak.js";
import { renderTeamEditor, renderTeams } from "./views/teams.js";
import { pokemonExtras, renderPokemon } from "./views/pokemon.js";
import { renderSearch } from "./views/search.js";
import { renderStats } from "./views/stats.js";

export function registerScreens() {
  // La ficha habla y suena su grito
  pokemonExtras.push(voiceButtons);

  route("/buscar", { title: "Buscar", render: renderSearch, nav: { label: "Buscar", icon: "search", order: 10, hint: "Los 151 de Kanto y más" } });
  route("/pokemon/:id", { title: "Ficha", render: renderPokemon, parent: "/buscar" });
  route("/pokedex", { title: "Mi Pokédex", render: renderCollection, nav: { label: "Mi Pokédex", icon: "book", order: 20, hint: "Tu colección" } });
  // «nuevo» va antes que «:id»: se comprueban en orden y «nuevo» no es un ID.
  route("/equipos", { title: "Equipos", render: renderTeams, nav: { label: "Equipos", icon: "swords", order: 30, hint: "Hasta seis y su análisis" } });
  route("/equipos/nuevo", { title: "Nuevo equipo", render: renderTeamEditor, parent: "/equipos" });
  route("/equipos/:id", { title: "Equipo", render: renderTeamEditor, parent: "/equipos" });
  route("/combate", { title: "Combate", render: renderBattleLobby, nav: { label: "Combate", icon: "bolt", order: 35, hint: "Gana experiencia y evoluciona" } });
  route("/combate/:id", { title: "Combate", render: renderBattle, parent: "/combate" });
  route("/oak", { title: "Profesor Oak", render: renderOak, nav: { label: "Profesor Oak", icon: "flask", order: 40, hint: "Pregúntale con tus palabras" } });
  route("/juego", { title: "Adivina la silueta", render: renderGame, nav: { label: "Juego", icon: "gamepad", order: 50, hint: "Adivina la silueta y captura" } });
  route("/estadisticas", { title: "Estadísticas", render: renderStats, nav: { label: "Estadísticas", icon: "chart", order: 60, hint: "Tu progreso en gráficos" } });
}
