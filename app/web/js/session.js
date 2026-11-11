// Estado compartido de la sesión de la web: quién ha entrado.

import { request } from "./api.js";

export const session = {
  user: null,
  // true justo después de crear la cuenta: el inicio da la bienvenida guiada
  isNew: false,
};

export async function loadUser() {
  session.user = await request("/api/v1/auth/me");
  return session.user;
}
