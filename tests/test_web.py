"""Web de la Pokédex servida en /: CSP, ficheros, reglas de seguridad del JavaScript y
cambios de /docs. La lógica del JavaScript se prueba aparte con ``node --test``."""

import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from app.config import get_settings
from app.main import WEB_DIR, create_app
from app.security_middleware import WEB_CSP

INDEX = (WEB_DIR / "index.html").read_text(encoding="utf-8")
JS_FILES = sorted(WEB_DIR.rglob("*.js"))
CSS_FILES = sorted(WEB_DIR.rglob("*.css"))


def csp_directives(header: str) -> dict[str, str]:
    return {
        part.strip().split(" ", 1)[0]: part.strip().partition(" ")[2]
        for part in header.split(";")
        if part.strip()
    }


# ═════════════════════════════════════════════════════════════
#  Servir la web
# ═════════════════════════════════════════════════════════════
def test_index_is_served_with_its_own_strict_csp(client: TestClient) -> None:
    response = client.get("/")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    assert response.headers["content-security-policy"] == WEB_CSP
    directives = csp_directives(WEB_CSP)
    assert directives["script-src"] == "'self'"  # sin scripts en línea, eval ni CDN
    assert directives["style-src"] == "'self'"
    assert directives["connect-src"] == "'self'"  # la web solo habla con su propia API
    assert "unsafe" not in WEB_CSP
    assert directives["frame-ancestors"] == "'none'"


def test_api_keeps_its_own_csp(client: TestClient) -> None:
    assert client.get("/health").headers["content-security-policy"].startswith("default-src 'none'")


def test_web_assets_are_revalidated_on_every_load(client: TestClient) -> None:
    response = client.get("/web/js/main.js")

    assert response.status_code == 200
    assert "javascript" in response.headers["content-type"]
    assert response.headers["cache-control"] == "no-cache"
    etag = response.headers["etag"]
    assert client.get("/web/js/main.js", headers={"If-None-Match": etag}).status_code == 304


def test_font_is_served_for_the_web(client: TestClient) -> None:
    assert client.get("/fonts/Nunito-Variable.ttf").status_code == 200


def test_web_can_be_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(get_settings(), "web_enabled", False)
    with TestClient(create_app()) as client:
        assert client.get("/").status_code == 404
        assert client.get("/web/js/main.js").status_code == 404


# ═════════════════════════════════════════════════════════════
#  Reglas del HTML, el CSS y el JavaScript
# ═════════════════════════════════════════════════════════════
def test_index_has_no_inline_code() -> None:
    """La CSP lo bloquearía; además, se comprueba aquí para detectarlo en la CI."""
    scripts = re.findall(r"<script\b([^>]*)>", INDEX)
    assert scripts, "la página debe cargar su JavaScript"
    assert all('src="' in attrs for attrs in scripts)
    assert "<style" not in INDEX
    assert not re.search(r"\sstyle=", INDEX)
    assert not re.search(r"\son[a-z]+=", INDEX)


def test_every_local_asset_of_the_index_exists(client: TestClient) -> None:
    assets = re.findall(r'(?:href|src)="(/(?:web|static|fonts)/[^"]+)"', INDEX)
    assert assets
    for asset in assets:
        assert client.get(asset).status_code == 200, asset


def test_every_javascript_import_resolves() -> None:
    for file in JS_FILES:
        source = file.read_text(encoding="utf-8")
        for target in re.findall(r"""(?:from|import)\s*\(?\s*["'](\.{1,2}/[^"']+)["']""", source):
            assert (file.parent / target).resolve().is_file(), f"{file.name}: {target}"


@pytest.mark.parametrize("file", JS_FILES, ids=lambda f: f.name)
def test_javascript_never_injects_html_or_evaluates_code(file: Path) -> None:
    """Todo el texto entra con textContent (dom.js): un apodo con «<script>» se muestra,
    no se ejecuta. Estas APIs abrirían la puerta al XSS."""
    source = file.read_text(encoding="utf-8")
    forbidden = (
        r"\.innerHTML|\.outerHTML|insertAdjacentHTML|document\.write|\beval\(|new Function\("
    )
    assert not re.search(forbidden, source)
    assert not re.search(r"localStorage\.setItem\([^)]*token", source, re.IGNORECASE)


@pytest.mark.parametrize("file", CSS_FILES, ids=lambda f: f.name)
def test_css_only_loads_own_resources(file: Path) -> None:
    urls = re.findall(r"url\(\s*[\"']?([^\"')]+)", file.read_text(encoding="utf-8"))
    assert all(url.startswith("/") for url in urls), urls


# ═════════════════════════════════════════════════════════════
#  Botón de Google en la web
# ═════════════════════════════════════════════════════════════
def test_google_status_without_credentials(client: TestClient) -> None:
    assert client.get("/api/v1/auth/google/status").json() == {"enabled": False}


def test_google_status_with_credentials(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "google_client_id", "id.apps.googleusercontent.com")
    monkeypatch.setattr(settings, "google_client_secret", SecretStr("secreto"))

    body = client.get("/api/v1/auth/google/status").json()

    assert body == {"enabled": True}  # sin ningún dato de las credenciales


# ═════════════════════════════════════════════════════════════
#  /docs pensado para quien llega nuevo
# ═════════════════════════════════════════════════════════════
def test_docs_page_links_back_to_the_web(client: TestClient) -> None:
    html = client.get("/docs").text

    assert 'href="/"' in html
    assert "/static/swagger-theme.css" in html
    assert client.get("/static/swagger-theme.css").status_code == 200


def test_openapi_has_a_quick_guide_and_described_groups(client: TestClient) -> None:
    schema = client.get("/openapi.json").json()

    assert "Guía rápida" in schema["info"]["description"]
    tags = {tag["name"]: tag["description"] for tag in schema["tags"]}
    assert {"Autenticación", "Pokémon", "Pokédex", "Equipos"} <= tags.keys()
    assert all(tags.values())


def test_internal_endpoints_are_hidden_from_swagger_but_still_work(client: TestClient) -> None:
    paths = client.get("/openapi.json").json()["paths"]

    assert "/health" not in paths  # para balanceadores y Docker, no para usuarios
    assert "/api/v1/auth/google/callback" not in paths  # solo la llama Google
    assert "/" not in paths
    assert client.get("/health").status_code == 200


def test_every_icon_used_exists() -> None:
    """Un nombre mal escrito no rompe nada visible (sale el icono de ayuda), así que se
    comprueba aquí: todo icono usado en la web debe existir en icons.js."""
    icons_source = (WEB_DIR / "js" / "icons.js").read_text(encoding="utf-8")
    defined = set(re.findall(r'^\s+"?([a-z-]+)"?: (?:\[|gear\(\))', icons_source, re.MULTILINE))
    used: set[str] = set()
    for file in JS_FILES:
        source = file.read_text(encoding="utf-8")
        used |= set(re.findall(r'\bicon\(\s*"([a-z-]+)"', source))
        used |= set(re.findall(r'\bicon: "([a-z-]+)"', source))  # menú y misiones
        used |= set(re.findall(r'\b(?:empty|tile|chips)\(\s*"([a-z-]+)"', source))
        used |= set(re.findall(r'adviceList\([^,]+,\s*"([a-z-]+)"', source))
    assert used, "no se ha encontrado ningún icono"
    assert used <= defined, sorted(used - defined)
