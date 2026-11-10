"""Página de documentación interactiva (Swagger UI) propia.

La página que genera FastAPI por defecto tiene tres inconvenientes:

1. Carga el icono desde ``fastapi.tiangolo.com``: cualquiera que abra /docs sabe con qué
   está hecha la API. Aquí se usa un icono propio servido por la propia API.
2. Pide Swagger UI al CDN con la versión «flotante» ``@5``, que puede cambiar sin aviso.
   Aquí la versión está fijada y cada archivo lleva su hash **SRI** (*Subresource
   Integrity*): si el CDN sirviera un archivo alterado, el navegador no lo ejecutaría.
3. Arranca Swagger con un script incrustado en el HTML, lo que obliga a que la CSP
   permita scripts en línea. Aquí el arranque está en ``/static/swagger-init.js``.
"""

from typing import Final

from fastapi import APIRouter
from fastapi.responses import HTMLResponse

from app.config import get_settings

SWAGGER_VERSION: Final = "5.34.1"
_CDN: Final = f"https://cdn.jsdelivr.net/npm/swagger-ui-dist@{SWAGGER_VERSION}"
SWAGGER_JS: Final = (
    f"{_CDN}/swagger-ui-bundle.js",
    "sha384-XFYN47VzPiDY6qPtxYKg0nqUeW/upo20xf1j2SUE7gT/i35FJB4cuS/71Iegzk8Z",
)
SWAGGER_CSS: Final = (
    f"{_CDN}/swagger-ui.css",
    "sha384-4c3vsE5f+trhFyHrFrLM1fFGUn531NIaC7dcqEWIJdoRQJ5CkJbSuSCwXLpaS1KF",
)

router = APIRouter(include_in_schema=False)


@router.get("/docs", response_class=HTMLResponse)
def swagger_ui() -> HTMLResponse:
    title = f"{get_settings().app_name} · Documentación"
    html = f"""<!doctype html>
<html lang="es">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta name="referrer" content="no-referrer">
  <title>{title}</title>
  <link rel="icon" type="image/svg+xml" href="/static/favicon.svg">
  <link rel="stylesheet" href="{SWAGGER_CSS[0]}" integrity="{SWAGGER_CSS[1]}"
        crossorigin="anonymous">
  <link rel="stylesheet" href="/static/swagger-theme.css">
</head>
<body>
  <header class="docs-banner">
    <a class="docs-banner__back" href="/">← Volver a la Pokédex</a>
    <span class="docs-banner__title">Documentación de la API</span>
  </header>
  <main id="swagger-ui"></main>
  <script src="{SWAGGER_JS[0]}" integrity="{SWAGGER_JS[1]}" crossorigin="anonymous"></script>
  <script src="/static/swagger-init.js"></script>
</body>
</html>"""
    return HTMLResponse(html)
