# Pokédex API

API REST para que cada entrenador gestione su **Pokédex personal**: busca Pokémon en la [PokeAPI](https://pokeapi.co/) pública, los guarda en su colección, los marca como capturados, consulta estadísticas y monta equipos de batalla de hasta seis Pokémon.

Práctica 1 del Módulo 2 (APIs REST y fundamentos de integración) de **Sistemas de Información**, Universidad Francisco de Vitoria.

> 🚧 **En construcción.** Este README crece con cada rama `features/` integrada en `develop`. Las secciones que aún no tienen contenido indican qué rama las completará.

## Equipo

Práctica **individual**, autorizada por el profesor.

| Miembro | Usuario Git | Partes desarrolladas |
|---|---|---|
| Sergio Zuñiga Casado | [@szunigacasado](https://github.com/szunigacasado) | Todo el proyecto: API, seguridad, integración con PokeAPI, tests, CI y documentación |

## Stack

| Capa | Tecnología |
|---|---|
| Framework | FastAPI |
| ORM y modelos | SQLModel (Pydantic + SQLAlchemy) |
| Base de datos | SQLite (PostgreSQL opcional) |
| Cliente HTTP | httpx (asíncrono) |
| Configuración | pydantic-settings, variables de entorno |
| Tests | pytest, pytest-asyncio, respx, pytest-cov |
| Calidad | ruff |
| Gestor de paquetes | uv |

## Estructura

```
pokedex-api/
├── app/
│   ├── routers/        # Endpoints agrupados por recurso
│   └── services/       # Clientes de APIs externas (PokeAPI)
├── tests/
├── .env.example        # Plantilla de variables de entorno
├── pyproject.toml      # Dependencias y configuración de herramientas (uv)
├── uv.lock             # Versiones exactas resueltas por uv
└── requirements.txt    # Exportado desde uv.lock para instalar con pip
```

## Instalación

Requisitos: Python 3.12 o superior y [uv](https://docs.astral.sh/uv/) (recomendado) o pip.

```bash
git clone https://github.com/szunigacasado/pokedex-api.git
cd pokedex-api
cp .env.example .env        # y rellena los valores
```

Con **uv** (recomendado):

```bash
uv sync
```

Con **pip**:

```bash
python -m venv .venv
source .venv/bin/activate   # en Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

## Configuración

Ver [`.env.example`](.env.example): cada variable está documentada en el propio fichero.

## Ejecución

```bash
uv run uvicorn app.main:app --reload --no-server-header
```

- Documentación interactiva (Swagger): <http://127.0.0.1:8000/docs>
- Estado del servicio: <http://127.0.0.1:8000/health>

La aplicación no arranca si falta `SECRET_KEY` o si tiene menos de 32 caracteres. En producción tampoco arranca con la clave de ejemplo de `.env.example`.

`--no-server-header` evita que las respuestas revelen qué servidor web se usa. El arranque con HTTPS en el puerto 443 se añade en `features/https-docker`.

## Testing

```bash
uv run pytest --cov=app --cov-report=term-missing
```

Cada test usa una base de datos SQLite en memoria creada desde cero, así que no deja ficheros ni toca datos reales. La integración continua se añade en `features/ci`.

## Flujo de trabajo y despliegue

| Rama | Para qué sirve |
|---|---|
| `main` | Versión estable y entregable. Solo recibe *pull requests* desde `develop`. |
| `develop` | Rama de integración de las funcionalidades terminadas. |
| `features/<nombre>` | Una rama por funcionalidad, creada desde `develop`. |
| `fix/<nombre>` | Correcciones de errores ya integrados. |

Cada funcionalidad sigue el ciclo *issue → rama → commits pequeños → pull request → revisión → integración*, con mensajes en formato [Conventional Commits](https://www.conventionalcommits.org/es/). El avance se sigue en el tablero de GitHub Projects del repositorio.

## Endpoints

_Se completa a medida que se integran los routers._

## Decisiones de Diseño

### Rate limiting
_Se completa en `features/rate-limiting-cors`._

### Versionado
_Se completa en `features/api-v2`._

### Otras decisiones
_Se completa durante el desarrollo._

## Decisiones de Seguridad

_Se resume aquí y se detalla en `SECURITY.md`._

## Mejoras Futuras

_Se completa al cerrar la versión `v1.0.0`._
