# Pokédex API

![CI](https://github.com/szunigacasado/pokedex-api/actions/workflows/ci.yml/badge.svg)

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

Cada test usa una base de datos SQLite en memoria creada desde cero, así que no deja ficheros ni toca datos reales.

### Integración continua

[`.github/workflows/ci.yml`](.github/workflows/ci.yml) se ejecuta en cada *push* a `main` o `develop` y en cada *pull request*. Un PR con la CI en rojo no se integra.

| Trabajo | Qué comprueba |
|---|---|
| **Calidad** | `ruff check` y `ruff format --check`; que `uv.lock` coincide con `pyproject.toml` y que `requirements.txt` coincide con `uv.lock`. |
| **Tests** | Instala desde `requirements.txt` y ejecuta `pytest --cov=app --cov-fail-under=80` en Python 3.12 y 3.13. La cobertura aparece en el resumen de la ejecución. |
| **Seguridad** | `gitleaks` busca secretos en **todo el historial** y `pip-audit` busca vulnerabilidades conocidas en las dependencias. |

Medidas de seguridad del propio flujo:

- Permisos de solo lectura.
- Acciones de terceros fijadas por SHA, para que una etiqueta movida no pueda inyectar código.
- Clave de pruebas propia de la CI, nunca la real.

[Dependabot](.github/dependabot.yml) propone cada semana actualizaciones de dependencias y de acciones, siempre como PR contra `develop`.

## Flujo de trabajo y despliegue

| Rama | Para qué sirve |
|---|---|
| `main` | Versión estable y entregable. Solo recibe *pull requests* desde `develop`. |
| `develop` | Rama de integración de las funcionalidades terminadas. |
| `features/<nombre>` | Una rama por funcionalidad, creada desde `develop`. |
| `fix/<nombre>` | Correcciones de errores ya integrados. |

Cada funcionalidad sigue el ciclo *issue → rama → commits pequeños → pull request → revisión → integración*, con mensajes en formato [Conventional Commits](https://www.conventionalcommits.org/es/). El avance se sigue en el tablero de GitHub Projects del repositorio.

## Endpoints

La documentación completa e interactiva está en **`/docs`** (Swagger). Para probar los endpoints protegidos, pulsa **Authorize** e introduce usuario (o email) y contraseña: Swagger obtiene el token y lo envía en cada petición.

### Autenticación (`/api/v1/auth`)

| Método | Ruta | Auth | Descripción |
|---|---|:-:|---|
| `POST` | `/register` | — | Crea una cuenta. Valida email con regex y la política de contraseñas. `201` o `409` si ya existe. |
| `POST` | `/login` | — | Formulario OAuth2 (`username`, `password`). Devuelve access token (24 h) y refresh token (7 días). `429` si la cuenta está bloqueada. |
| `POST` | `/refresh` | — | Cambia un refresh token por un par nuevo (rotación). Reutilizar uno ya usado revoca la sesión entera. |
| `POST` | `/logout` | — | Revoca el refresh token y los de su misma sesión. `204`. |
| `GET` | `/me` | 🔒 | Datos del usuario autenticado. |

```bash
# Registro
curl -X POST http://127.0.0.1:8000/api/v1/auth/register \
  -H "Content-Type: application/json" \
  -d '{"username": "ash", "email": "ash@kanto.dev", "password": "Pikachu123"}'

# Login (formulario OAuth2)
curl -X POST http://127.0.0.1:8000/api/v1/auth/login \
  -d "username=ash&password=Pikachu123"

# Endpoint protegido
curl http://127.0.0.1:8000/api/v1/auth/me -H "Authorization: Bearer <access_token>"
```

## Decisiones de Diseño

### Rate limiting
_Se completa en `features/rate-limiting-cors`._

### Versionado
_Se completa en `features/api-v2`._

### Otras decisiones
_Se completa durante el desarrollo._

## Decisiones de Seguridad

_Resumen; el detalle completo irá en `SECURITY.md`._

### Autenticación

| Medida | Por qué |
|---|---|
| **bcrypt** (coste 12) vía passlib | Hash lento y con sal: un volcado de la base de datos no revela las contraseñas. |
| Contraseña de 72 bytes como máximo | bcrypt ignora en silencio lo que pasa de 72 bytes; se rechaza en lugar de truncarla. |
| Política: 8+ caracteres, mayúscula, número y sin el nombre de usuario | Requisito del enunciado, más una regla barata contra contraseñas obvias. |
| Mismo mensaje y mismo tiempo para "usuario inexistente" y "contraseña incorrecta" | Evita descubrir qué usuarios existen. Si el usuario no existe, se verifica igualmente contra un hash ficticio. |
| **Bloqueo temporal** tras 5 fallos seguidos (`429` + `Retry-After`) | Límite de reintentos por cuenta: frena la fuerza bruta aunque el atacante cambie de IP. |
| JWT con `sub`, `user_id`, `exp`, `iat`, `nbf`, `iss`, `aud`, `jti` y `type` | Validación según RFC 8725: algoritmo fijado al decodificar (rechaza `alg: none` y la confusión de algoritmo), emisor y audiencia comprobados. |
| Access token de 24 h + refresh token de 7 días **con rotación** | Cada refresh token sirve una vez. Si se reutiliza uno, se interpreta como robo y se revoca toda la sesión. |
| El usuario se vuelve a comprobar en la base de datos en cada petición | Un token de un usuario borrado o desactivado deja de valer al momento. |
| `Cache-Control: no-store` en las respuestas con tokens | Ningún proxy ni navegador guarda los tokens en caché. |
| Rehash automático al iniciar sesión | Si se sube el coste de bcrypt, las contraseñas antiguas se actualizan solas. |

### Vulnerabilidades conocidas en dependencias

`pip-audit` señala dos avisos que **no afectan** a esta API. Se ignoran en la CI de forma explícita y justificada:

- **`ecdsa`** (ataque Minerva): afecta a *firmar* con ECDSA. La API solo firma con HMAC-SHA256.
- **`python-jose`** (confusión de algoritmo): requiere una clave pública y no restringir los algoritmos. Aquí la clave es simétrica y `decode_token` fija `algorithms=["HS256"]`. Hay tests que intentan el ataque.

`python-jose` es obligatorio según el enunciado. En un proyecto real se sustituiría por `PyJWT` o `joserfc`.

## Mejoras Futuras

_Se completa al cerrar la versión `v1.0.0`._
