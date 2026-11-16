# syntax=docker/dockerfile:1
# ═════════════════════════════════════════════════════════════
#  Pokédex API · imagen de producción
#  Dos etapas: la primera instala las dependencias con uv; la final solo lleva el
#  entorno virtual y el código, y se ejecuta con un usuario sin privilegios.
#  Las imágenes base se fijan por digest (no por etiqueta, que puede moverse) y
#  Dependabot las mantiene actualizadas.
# ═════════════════════════════════════════════════════════════

FROM ghcr.io/astral-sh/uv:0.13.7@sha256:96f40d3904aad8d0bd010539001d36c8e2188b981e1334471a90c4a1d93bb490 AS uv

# ── Etapa 1: dependencias ────────────────────────────────────
FROM python:3.15-slim@sha256:65a65fc7b30b06cb6df4930fd4650697cb23f4c2698ab81fc87e4b031bc36928 AS builder

COPY --from=uv /uv /bin/uv
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never

WORKDIR /app
COPY pyproject.toml uv.lock ./
# --locked: falla si uv.lock no coincide con pyproject.toml, en lugar de resolver de nuevo
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev --no-install-project

# ── Etapa 2: imagen final ────────────────────────────────────
FROM python:3.15-slim@sha256:65a65fc7b30b06cb6df4930fd4650697cb23f4c2698ab81fc87e4b031bc36928

ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    DATABASE_URL=sqlite:////data/pokedex.db \
    LOG_FILE="" \
    FORWARDED_ALLOW_IPS=127.0.0.1

# Usuario sin privilegios ni shell; /data es lo único en lo que puede escribir
RUN groupadd --system --gid 10001 pokedex \
    && useradd --system --uid 10001 --gid pokedex --no-create-home \
       --shell /usr/sbin/nologin pokedex \
    && mkdir /data && chown pokedex:pokedex /data

WORKDIR /app
COPY --from=builder /app/.venv /app/.venv
COPY app ./app

USER pokedex
EXPOSE 8000
VOLUME ["/data"]

HEALTHCHECK --interval=10s --timeout=3s --start-period=10s --retries=3 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=2)"]

# Detrás de Caddy, que termina TLS en el puerto 443. --proxy-headers hace que la API vea
# la IP real del cliente, pero solo si la petición viene de FORWARDED_ALLOW_IPS (el
# proxy); compose.yaml le da la IP fija de Caddy.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", \
     "--proxy-headers", "--no-server-header", "--no-access-log"]
