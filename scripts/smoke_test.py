"""Prueba de humo: recorre todos los endpoints de una API en marcha.

Crea un usuario nuevo, usa cada endpoint como lo haría un cliente y comprueba el código
de respuesta y lo esencial del contenido. Termina con un resumen y sale con error si algo
ha fallado, así que también sirve en un script o en la CI.

    uv run python -m app                                   # en otra terminal
    uv run python -m scripts.smoke_test                    # https://localhost con certs/ca.pem
    uv run python -m scripts.smoke_test --base-url http://127.0.0.1:8000
"""

import argparse
import secrets
import ssl
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import httpx


@dataclass
class Result:
    method: str
    path: str
    status: int | None
    ok: bool
    ms: float
    note: str = ""


class Smoke:
    def __init__(self, client: httpx.Client) -> None:
        self.client = client
        self.results: list[Result] = []
        self.headers: dict[str, str] = {}

    def check(
        self,
        method: str,
        path: str,
        expected: int | tuple[int, ...],
        validate: Callable[[httpx.Response], str | None] | None = None,
        *,
        auth: bool = True,
        label: str | None = None,
        **kwargs: object,
    ) -> httpx.Response | None:
        """Hace la petición y registra si el código y el contenido son los esperados.

        ``validate`` devuelve ``None`` si el contenido es correcto, o el motivo del fallo.
        """
        expected = (expected,) if isinstance(expected, int) else expected
        headers = self.headers if auth else {}
        start = time.perf_counter()
        try:
            response = self.client.request(method, path, headers=headers, **kwargs)  # type: ignore[arg-type]
        except httpx.HTTPError as exc:
            self.results.append(Result(method, label or path, None, False, 0, str(exc)))
            return None
        ms = (time.perf_counter() - start) * 1000
        problem = None
        if response.status_code not in expected:
            problem = f"esperado {'/'.join(map(str, expected))}: {response.text[:120]}"
        elif "server" in response.headers:
            problem = "revela la cabecera Server"
        elif validate is not None:
            problem = validate(response)
        self.results.append(
            Result(method, label or path, response.status_code, problem is None, ms, problem or "")
        )
        return response

    def report(self) -> bool:
        width = max(len(f"{r.method} {r.path}") for r in self.results)
        for r in self.results:
            mark = "OK " if r.ok else "ERR"
            status = r.status if r.status is not None else "---"
            line = f"{mark} {f'{r.method} {r.path}':<{width}}  {status}  {r.ms:6.0f} ms"
            print(line + (f"  {r.note}" if r.note else ""))
        failed = [r for r in self.results if not r.ok]
        print(f"\n{len(self.results) - len(failed)}/{len(self.results)} comprobaciones correctas")
        return not failed


def has(*keys: str) -> Callable[[httpx.Response], str | None]:
    def validate(response: httpx.Response) -> str | None:
        missing = [k for k in keys if k not in response.json()]
        return f"faltan {missing}" if missing else None

    return validate


def content_type(prefix: str) -> Callable[[httpx.Response], str | None]:
    def validate(response: httpx.Response) -> str | None:
        value = response.headers.get("content-type", "")
        return None if value.startswith(prefix) else f"content-type {value!r}"

    return validate


def run(smoke: Smoke) -> None:
    s = smoke
    user = f"smoke_{secrets.token_hex(4)}"
    # Contraseña de un solo uso que cumple la política (mayúscula, número, 8+ caracteres)
    password = f"Pk{secrets.token_urlsafe(12)}7"

    # ── Salud y autenticación ────────────────────────────────
    s.check("GET", "/health", 200, has("status"), auth=False)
    s.check(
        "POST",
        "/api/v1/auth/register",
        201,
        lambda r: "devuelve el hash" if "hashed_password" in r.json() else None,
        auth=False,
        json={"username": user, "email": f"{user}@kanto.dev", "password": password},
    )
    login = s.check(
        "POST",
        "/api/v1/auth/login",
        200,
        has("access_token", "refresh_token", "expires_in"),
        auth=False,
        data={"username": user, "password": password},
    )
    if login is None or login.status_code != 200:
        return
    tokens = login.json()
    s.headers = {"Authorization": f"Bearer {tokens['access_token']}"}
    s.check("GET", "/api/v1/auth/me", 200, has("username", "email"))
    s.check("GET", "/api/v1/auth/me", 401, auth=False, label="/api/v1/auth/me (sin token)")
    s.check("GET", "/api/v1/auth/google/status", 200, has("enabled"), auth=False)
    s.check(
        "GET",
        "/api/v1/auth/google/login",
        (302, 503),  # 302 hacia Google si hay credenciales; 503 si no están configuradas
        auth=False,
        follow_redirects=False,
    )
    s.check(
        "GET",
        "/api/v1/auth/google/callback",
        (400, 503),  # 400 por falta de la cookie de login; 503 si Google no está configurado
        auth=False,
        params={"code": "x", "state": "y"},
        label="/api/v1/auth/google/callback (sin cookie)",
    )

    # ── Pokémon (PokeAPI) ────────────────────────────────────
    s.check("GET", "/api/v1/pokemon/search", 200, has("results"), params={"name": "char"})
    s.check("GET", "/api/v1/pokemon/pikachu", 200, has("id", "types", "stats"))
    s.check("GET", "/api/v1/pokemon/no-existe", 404, label="/api/v1/pokemon/{id} (inexistente)")
    s.check("GET", "/api/v1/pokemon/25/card", 200, content_type("image/png"))
    s.check(
        "GET",
        "/api/v1/pokemon/25/card",
        200,
        content_type("application/pdf"),
        params={"format": "pdf"},
        label="/api/v1/pokemon/{id}/card?format=pdf",
    )
    s.check(
        "GET",
        "/api/v1/pokemon/ask",
        200,
        has("results", "source"),
        params={"q": "los 3 de fuego más rápidos"},
    )

    # ── Pokédex ──────────────────────────────────────────────
    entries = []
    for pokemon_id in (25, 6, 9):
        r = s.check(
            "POST",
            "/api/v1/pokedex",
            201,
            has("id", "pokemon_name"),
            json={"pokemon_id": pokemon_id, "is_captured": True},
            label=f"/api/v1/pokedex ({pokemon_id})",
        )
        if r is not None and r.status_code == 201:
            entries.append(r.json()["id"])
    s.check(
        "POST", "/api/v1/pokedex", 409, json={"pokemon_id": 25}, label="/api/v1/pokedex (duplicado)"
    )
    s.check("GET", "/api/v1/pokedex", 200, params={"captured": "true", "sort": "pokemon_id"})
    if entries:
        entry = entries[0]
        s.check(
            "GET", f"/api/v1/pokedex/{entry}", 200, has("nickname"), label="/api/v1/pokedex/{id}"
        )
        s.check(
            "PATCH",
            f"/api/v1/pokedex/{entry}",
            200,
            lambda r: None if r.json()["nickname"] == "Sparky" else "no guarda el apodo",
            json={"nickname": "Sparky", "favorite": True},
            label="/api/v1/pokedex/{id}",
        )
    s.check(
        "GET", "/api/v1/pokedex/stats", 200, has("total_pokemon"), params={"tz": "Europe/Madrid"}
    )
    s.check("GET", "/api/v1/pokedex/export", 200, content_type("text/csv"))
    s.check(
        "GET",
        "/api/v1/pokedex/export",
        200,
        content_type("application/pdf"),
        params={"format": "pdf"},
        label="/api/v1/pokedex/export?format=pdf",
    )
    s.check(
        "GET", "/api/v1/pokedex/999999", 404, label="/api/v1/pokedex/{id} (ajeno o inexistente)"
    )

    # ── Equipos e IA ─────────────────────────────────────────
    team = s.check(
        "POST",
        "/api/v1/teams",
        201,
        has("id", "members"),
        json={"name": f"Equipo {user}", "description": "Prueba de humo", "pokemon_ids": [25, 6, 9]},
    )
    s.check("GET", "/api/v1/teams", 200)
    if team is not None and team.status_code == 201:
        team_id = team.json()["id"]
        s.check("GET", f"/api/v1/teams/{team_id}", 200, label="/api/v1/teams/{id}")
        s.check(
            "PUT",
            f"/api/v1/teams/{team_id}",
            200,
            json={"name": f"Equipo final {user}", "pokemon_ids": [6, 25]},
            label="/api/v1/teams/{id}",
        )
        s.check(
            "GET",
            f"/api/v1/teams/{team_id}/analysis",
            200,
            has("defensive"),
            label="/api/v1/teams/{id}/analysis",
        )
        s.check(
            "GET",
            f"/api/v1/teams/{team_id}/export",
            200,
            content_type("application/pdf"),
            label="/api/v1/teams/{id}/export",
        )
        s.check(
            "GET",
            f"/api/v1/teams/{team_id}/advice",
            200,
            has("summary", "suggestions", "source"),
            label="/api/v1/teams/{id}/advice",
        )
        s.check("DELETE", f"/api/v1/teams/{team_id}", 204, label="/api/v1/teams/{id}")

    # ── v2 ───────────────────────────────────────────────────
    s.check("GET", "/api/v2/pokedex", 200, has("items", "next_cursor"), params={"limit": 2})
    s.check("GET", "/api/v2/pokemon/eevee/evolution", 200, label="/api/v2/pokemon/{id}/evolution")

    # ── Limpieza y fin de sesión ─────────────────────────────
    for entry in entries:
        s.check(
            "DELETE", f"/api/v1/pokedex/{entry}", 204, label=f"/api/v1/pokedex/{{id}} ({entry})"
        )
    refreshed = s.check(
        "POST",
        "/api/v1/auth/refresh",
        200,
        has("access_token"),
        auth=False,
        json={"refresh_token": tokens["refresh_token"]},
    )
    s.check(
        "POST",
        "/api/v1/auth/refresh",
        401,
        auth=False,
        json={"refresh_token": tokens["refresh_token"]},
        label="/api/v1/auth/refresh (reutilizado)",
    )
    if refreshed is not None and refreshed.status_code == 200:
        s.check(
            "POST",
            "/api/v1/auth/logout",
            204,
            auth=False,
            json={"refresh_token": refreshed.json()["refresh_token"]},
        )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--base-url", default="https://localhost")
    parser.add_argument(
        "--cacert",
        default="certs/ca.pem",
        help="CA en la que confiar por HTTPS (la que genera python -m app).",
    )
    args = parser.parse_args()

    verify: ssl.SSLContext | bool = True
    if args.base_url.startswith("https://") and Path(args.cacert).is_file():
        verify = ssl.create_default_context(cafile=args.cacert)
    with httpx.Client(base_url=args.base_url, verify=verify, timeout=30) as client:
        smoke = Smoke(client)
        run(smoke)
    sys.exit(0 if smoke.report() else 1)


if __name__ == "__main__":
    main()
