# Consideraciones de Seguridad

Cómo se protegen las cuentas, los datos y el servidor de la Pokédex API, qué riesgos del **OWASP API Security Top 10 (2023)** se han considerado y cómo se mitigan, y qué riesgos se han aceptado a sabiendas. Cada medida enlaza al código que la aplica y a los tests que la comprueban: ninguna se afirma sin una prueba automática detrás.

## Autenticación

Implementación en [`app/auth.py`](app/auth.py) y [`app/routers/auth.py`](app/routers/auth.py), con `python-jose`.

**JWT firmados con HS256** (HMAC-SHA256) y validados según el RFC 8725:

| Claim | Para qué |
|---|---|
| `sub`, `user_id` | Quién es el usuario. Se vuelve a buscar en la base de datos en cada petición: un usuario borrado o desactivado pierde el acceso al momento. |
| `exp`, `iat`, `nbf` | Caducidad, fecha de emisión y «no antes de». |
| `iss`, `aud` | Emisor y destinatario: un token emitido para otro servicio no sirve aquí. |
| `jti` | Identificador único, con el que se rastrea cada refresh token. |
| `type` | `access` o `refresh`: uno no puede usarse en lugar del otro. |

Al decodificar, el algoritmo está **fijado** (`algorithms=["HS256"]`). Así se rechazan los tokens sin firma (`alg: none`) y la confusión de algoritmo.

**Política de expiración:**

| Token | Duración | Comportamiento |
|---|---|---|
| Access | **24 horas** (`ACCESS_TOKEN_EXPIRE_MINUTES`) | Va en `Authorization: Bearer` en cada petición. |
| Refresh | **7 días** (`REFRESH_TOKEN_EXPIRE_DAYS`) | Sirve **una sola vez**: `POST /auth/refresh` devuelve un par nuevo y revoca el anterior (rotación). |

- **Reutilización de un refresh token:** todos los refresh tokens de un mismo inicio de sesión forman una familia. Si alguien presenta uno ya usado, se interpreta como robo y se revoca la familia entera: ni el atacante ni el usuario legítimo pueden seguir sin volver a entrar.
- **`POST /auth/logout`:** revoca la familia.
- **Sesión compartida entre `/docs` y la web:** al entrar desde el propio sitio, el refresh token también se guarda en una cookie `HttpOnly` (ver «Vulnerabilidades Conocidas → Más allá del Top 10»). Así, pulsar *Authorize* en `/docs` deja la sesión iniciada en la web.
- **Respuestas con tokens:** llevan `Cache-Control: no-store`.

**Inicio de sesión con Google** (OpenID Connect) en [`app/services/google_oauth.py`](app/services/google_oauth.py):
- PKCE (S256), `state` y `nonce`.
- Verificación completa del ID token: firma RS256 con las claves de Google, `iss`, `aud`, `exp`, `at_hash` y `email_verified`.
- Las cuentas **no se vinculan por email**, para impedir el *pre-account hijacking*.
- Tras el login se emiten los mismos tokens propios.

## Contraseñas

- **Algoritmo: bcrypt** con coste 12 (2¹² iteraciones), mediante passlib.
  - Es lento a propósito y lleva sal propia, así que un volcado de la base de datos no permite recuperar las contraseñas ni usar tablas precalculadas.
  - Si se sube el coste, cada contraseña se vuelve a calcular con el nuevo al iniciar sesión.
- **Política:** al menos 8 caracteres, con una mayúscula y un número, y sin contener el nombre de usuario.
- **Límite de 72 bytes:** bcrypt ignora en silencio lo que pase de ahí, así que se rechaza en lugar de truncar.
- **Nunca se exponen:** ni el hash ni la contraseña aparecen en las respuestas, los logs o los errores de validación.
- **Sin pistas para un atacante:** «usuario inexistente» y «contraseña incorrecta» dan la misma respuesta y tardan lo mismo (se verifica contra un hash ficticio).
- **Bloqueo:** 5 fallos seguidos bloquean la cuenta 15 minutos (`429` con `Retry-After`), aunque el atacante cambie de IP.

## Rate Limiting

Con slowapi y una ventana deslizante ([`app/rate_limit.py`](app/rate_limit.py)). Todos los límites se configuran con variables `RATE_LIMIT_*`.

| Endpoints | Límite | Clave | Justificación |
|---|---|---|---|
| `POST /auth/register` | **5/hora** | IP | Una persona se registra una vez; frena la creación masiva de cuentas. |
| `POST /auth/login` | **10/minuto** | IP | Permite equivocarse, pero hace inviable la fuerza bruta. Se suma al bloqueo por cuenta. |
| `/auth/refresh`, `/auth/logout` | 30/minuto | IP | Uso normal de un cliente con varias pestañas. |
| `/pokemon/*`, `POST /pokedex`, análisis de equipos | **30/minuto** | usuario | Cada petición genera llamadas a PokeAPI, un servicio externo y gratuito. |
| IA (`/teams/{id}/advice`, `/pokemon/ask`) | **10/minuto** | usuario | Cada llamada consume cuota de Groq. |
| Exportaciones CSV y PDF | 10/minuto | usuario | Generar un PDF con imágenes es lo más caro en CPU. |
| Resto de autenticados | 100/minuto | usuario | Consultas baratas; corta bucles descontrolados. |

- **Por usuario, no por IP**, en los endpoints autenticados: varios alumnos tras la misma red no se quitan el cupo entre ellos, y nadie se salta su límite cambiando de IP.
- **Al superarlo:** `429` con `Retry-After`. Todas las respuestas llevan `X-RateLimit-*` y el exceso queda registrado en el log.
- **IP real del cliente:** sin proxy no se acepta `X-Forwarded-For` de nadie. Detrás de Caddy, solo de su IP fija. Así no se puede falsificar la IP para saltarse los límites.

## CORS

- **Orígenes permitidos:** solo los de `CORS_ORIGINS`. Por defecto, los servidores de desarrollo de React (`http://localhost:3000`) y Vite (`http://localhost:5173`).
- **Métodos y cabeceras:** solo los que usa la API (`Authorization` y `Content-Type`).
- **`allow_credentials=False`:** la autenticación va en una cabecera y no en cookies.
- **`*` prohibido en producción:** la aplicación no arranca con `CORS_ORIGINS=*` y `ENVIRONMENT=production`.

**Por qué es necesario:** sin CORS, cualquier web que visitase un usuario con la sesión abierta podría usar su navegador para leer sus datos de la API. CORS indica al navegador qué orígenes pueden leer las respuestas. No sustituye a la autenticación: las peticiones desde `curl` o desde un servidor no pasan por CORS, y para eso están el JWT y el rate limiting.

## Variables de Entorno

**Información sensible que se guarda en `.env`:**

| Variable | Qué es | Si se filtra |
|---|---|---|
| `SECRET_KEY` | Clave que firma todos los JWT | Cualquiera podría crear tokens válidos para cualquier usuario. Es la más crítica. |
| `GOOGLE_CLIENT_SECRET` | Secreto de la aplicación en Google | Se podría suplantar a la aplicación ante Google. |
| `GROQ_API_KEY` | Clave de la IA | Consumo de la cuota a nuestra costa. |
| `DATABASE_URL` | Conexión a la base de datos | Con PostgreSQL incluiría usuario y contraseña. |

**Cómo se protege:**

- **Nada de secretos en el repositorio:**
  - `.env` está en `.gitignore`.
  - `.env.example` solo lleva valores de ejemplo.
  - `gitleaks` revisa **todo el historial** en cada PR.
- **`SECRET_KEY`:**
  - Obligatoria, de 32 caracteres como mínimo.
  - En producción se rechaza la de ejemplo.
  - Nunca aparece en `repr`, en los logs ni en los errores.
- **Claves de Google y Groq:** son `SecretStr`, se redactan en los logs y no se envían a ningún otro servicio.
- **Imagen de Docker:** no contiene el `.env`, porque `.dockerignore` lo excluye todo salvo `app/` y las dependencias. Los secretos llegan como variables de entorno al arrancar.
- **Certificado local:** la clave privada de la CA generada por `python -m app` **no se escribe nunca en disco**, y la CA lleva *Name Constraints* que la limitan a `localhost`.
- **Si una clave se filtra, se rota:**
  - Al cambiar `SECRET_KEY` se invalidan todos los tokens emitidos.
  - Las claves de Google y Groq se revocan desde sus consolas.
  - Revertir un commit no basta: el secreto sigue en el historial.

## Vulnerabilidades Conocidas

### Modelo de amenazas

**Qué se protege:**
- Las cuentas: contraseñas, tokens y sesiones.
- Los datos privados de cada entrenador: su Pokédex y sus equipos.
- Los secretos del servidor: `SECRET_KEY` y las claves de Google y Groq.
- La disponibilidad del servicio y la cuota de las APIs externas (PokeAPI y Groq).

**De quién:**

| Actor | Qué puede intentar |
|---|---|
| Anónimo en Internet | Fuerza bruta contra el login, enumeración de usuarios, abuso del registro, peticiones enormes, cabeceras `Host` o `X-Forwarded-For` falsificadas. |
| Usuario autenticado | Leer o modificar datos de otro usuario cambiando un ID, asignarse campos que no le corresponden, agotar recursos, inyectar instrucciones a la IA, fórmulas en los CSV. |
| Servicio externo comprometido o caído | Respuestas malformadas o enormes, lentitud, URLs de imágenes hacia la red interna, un ID token de Google falsificado. |
| Cadena de suministro | Una dependencia, una acción de GitHub o una imagen de Docker alteradas. |

**Superficie expuesta:**
- Solo el puerto 443, con HTTPS. El puerto 80 únicamente redirige.
- Con Docker, la API ni siquiera publica puertos: solo se llega a ella a través de Caddy.
- `/docs` y `/openapi.json` se pueden desactivar con `DOCS_ENABLED=false`.

### OWASP API Security Top 10 (2023)

| Riesgo | Medidas | Código | Tests |
|---|---|---|---|
| **API1 · Broken Object Level Authorization** | Todas las consultas filtran por el dueño (`owner_id`) en la propia consulta SQL, no después. Un recurso de otro usuario responde **404 y no 403**, para no confirmar que existe. | [`routers/pokedex.py`](app/routers/pokedex.py), [`routers/teams.py`](app/routers/teams.py) | `test_cannot_modify_other_user_pokedex`, `test_other_users_teams_are_invisible`, `test_advice_of_someone_elses_team_is_404` |
| **API2 · Broken Authentication** | <ul><li>bcrypt con coste 12; por encima de 72 bytes se rechaza la contraseña en vez de truncarla.</li><li>Misma respuesta y mismo tiempo si el usuario no existe (hash ficticio).</li><li>Bloqueo de la cuenta tras 5 fallos.</li><li>JWT según RFC 8725, con el algoritmo fijado y `iss`, `aud`, `nbf` y `type` validados.</li><li>Rotación del refresh token con detección de reutilización.</li><li>Login con Google por OIDC con PKCE, `state` y `nonce`, sin vincular cuentas por email.</li></ul> | [`auth.py`](app/auth.py), [`routers/auth.py`](app/routers/auth.py), [`services/google_oauth.py`](app/services/google_oauth.py) | `test_login_unknown_user_gives_the_same_answer`, `test_account_is_locked_after_repeated_failures`, `test_unsigned_token_alg_none_is_rejected`, `test_token_for_another_audience_is_rejected`, `test_reused_refresh_token_revokes_the_whole_session`, `test_state_mismatch_is_rejected`, `test_existing_local_account_is_not_hijacked` |
| **API3 · Broken Object Property Level Authorization** | <ul><li>Esquemas de entrada y salida separados: `UserCreate` y `UserRead` nunca incluyen `hashed_password`, `owner_id` ni `id`.</li><li>Los campos que no están en el esquema de entrada se ignoran (sin *mass assignment*).</li><li>Las respuestas usan `response_model`, así que solo sale lo declarado.</li></ul> | [`models.py`](app/models.py) | `test_register_user_success`, `test_incoherent_updates_are_rejected` |
| **API4 · Unrestricted Resource Consumption** | <ul><li>Rate limiting por usuario o IP (registro 5/hora, login 10/min, IA 10/min, global 100/min).</li><li>Cuerpos de más de 1 MB rechazados **mientras se reciben**, también con `chunked`.</li><li>Paginación de 100 elementos como máximo.</li><li>*Timeouts*, semáforo de concurrencia y caché hacia PokeAPI y Groq.</li></ul> | [`rate_limit.py`](app/rate_limit.py), [`security_middleware.py`](app/security_middleware.py), [`services/pokeapi_service.py`](app/services/pokeapi_service.py) | `test_rate_limit_exceeded`, `test_chunked_oversized_body_is_cut_while_streaming`, `test_list_rejects_invalid_parameters`, `test_ai_answers_are_cached` |
| **API5 · Broken Function Level Authorization** | No hay funciones de administración expuestas. Todo salvo `/health` y los endpoints de registro, login y renovación exige un token de **acceso**, catálogo de Pokémon incluido: un refresh token no sirve como access token, ni al revés. | [`dependencies.py`](app/dependencies.py) | `test_refresh_token_cannot_be_used_as_access_token`, `test_access_token_cannot_be_used_to_refresh`, `test_pokedex_requires_authentication` |
| **API6 · Unrestricted Access to Sensitive Business Flows** | <ul><li>El registro y la IA, que tienen coste, llevan límites propios y más estrictos que el global.</li><li>`UNIQUE` en la base de datos para que dos peticiones simultáneas no creen duplicados.</li></ul> | [`rate_limit.py`](app/rate_limit.py), [`models.py`](app/models.py) | `test_add_duplicate_pokemon`, `test_names_are_unique_ignoring_case_and_accents_casing` |
| **API7 · Server Side Request Forgery** | <ul><li>La API nunca pide una URL elegida por el usuario.</li><li>Las imágenes que vienen en las respuestas de PokeAPI solo se descargan de `raw.githubusercontent.com/PokeAPI/sprites/`, como mucho 2 MB y solo si la respuesta declara un tipo de imagen.</li><li>La URL base de PokeAPI es fija.</li></ul> | [`services/pokeapi_service.py`](app/services/pokeapi_service.py) | `test_images_outside_allowlist_are_never_fetched`, `test_images_are_cached_and_validated` |
| **API8 · Security Misconfiguration** | <ul><li>Cabeceras de seguridad en todas las respuestas, CSP estricta y HSTS solo por HTTPS.</li><li>Sin cabeceras `Server` ni `Via`.</li><li>`TrustedHost` y CORS sin comodines en producción; la app no arranca con una configuración insegura.</li><li>Errores 500 genéricos, y los 422 no devuelven lo que envió el cliente.</li><li>Logs con los secretos redactados.</li></ul> | [`security_middleware.py`](app/security_middleware.py), [`config.py`](app/config.py), [`errors.py`](app/errors.py), [`logging_config.py`](app/logging_config.py) | `test_every_response_has_security_headers`, `test_no_server_or_technology_is_revealed`, `test_unknown_host_is_rejected`, `test_cors_wildcard_is_forbidden_in_production`, `test_example_secret_key_is_rejected_in_production`, `test_unhandled_errors_return_a_safe_500`, `test_validation_errors_do_not_echo_the_input`, `test_secrets_are_redacted` |
| **API9 · Improper Inventory Management** | <ul><li>Una sola versión publicada por ruta (`/api/v1`, `/api/v2`).</li><li>Las rutas obsoletas lo anuncian con `Deprecation`, `Sunset` y `Link`, y responden `410 Gone` tras su fecha de retirada.</li><li>OpenAPI generado desde el código, así que no puede quedar desactualizado.</li></ul> | [`routers/v2.py`](app/routers/v2.py), [`main.py`](app/main.py) | `test_v1_list_announces_its_deprecation`, `test_openapi_marks_v1_list_as_deprecated`, `test_v1_is_gone_after_sunset` |
| **API10 · Unsafe Consumption of APIs** | <ul><li>Todo lo que llega de fuera se valida con Pydantic antes de usarse.</li><li>PokeAPI: reintentos con *backoff*, respetando `Retry-After` con un tope, y *circuit breaker*.</li><li>El ID token de Google se verifica entero (firma RS256 fijada, `iss`, `aud`, `exp`, `nonce`, `at_hash`).</li><li>La salida de la IA se valida contra un esquema cerrado y, si no encaja, se usan reglas.</li></ul> | [`services/pokeapi_service.py`](app/services/pokeapi_service.py), [`services/google_oauth.py`](app/services/google_oauth.py), [`services/ai_oak.py`](app/services/ai_oak.py) | `test_circuit_breaker_stops_calling_a_failing_api`, `test_retry_after_header_is_respected_and_capped`, `test_malformed_id_token`, `test_google_key_rotation`, `test_ai_filters_outside_the_schema_are_discarded`, `test_user_text_never_reaches_the_model` |

#### Más allá del Top 10

| Riesgo | Medida | Tests |
|---|---|---|
| Inyección de fórmulas en CSV | Las celdas que empiezan por `=`, `+`, `-`, `@`, tabulador o retorno de carro se neutralizan con una comilla. | `test_csv_cells_are_neutralized` |
| *Prompt injection* y *excessive agency* (OWASP LLM) | <ul><li>El consejo de la IA no recibe ningún texto escrito por el usuario. La búsqueda en lenguaje natural solo puede producir filtros de una lista cerrada.</li><li>Al asistente no le llegan los apodos ni las notas de la Pokédex.</li><li>Sus herramientas **solo leen**, con la sesión del propio usuario: no puede ver datos de otros.</li><li>Para cambiar algo solo puede **proponer** una acción, que el servidor valida y la web enseña como un botón: nada cambia hasta que el usuario lo pulsa, y entonces pasa por los endpoints normales.</li><li>Herramientas desconocidas o argumentos inválidos se rechazan; como mucho 4 rondas por mensaje y 4 propuestas por respuesta.</li></ul> | `test_user_text_never_reaches_the_model`, `test_ai_uses_tools_and_proposes_actions`, `test_ai_cannot_propose_nonsense`, `test_unknown_tools_and_bad_arguments_are_reported_to_the_model`, `test_endless_tool_calls_end_in_the_rules` |
| Redirección abierta | La redirección de HTTP a HTTPS solo va a hosts de `ALLOWED_HOSTS`. | `test_redirect_refuses_unknown_hosts` |
| IP falsificada con `X-Forwarded-For` | Sin proxy no se acepta de nadie. Con Docker, solo de la IP fija de Caddy. Desplegada, la IP sale de `CF-Connecting-IP`, que escribe Cloudflare (delante de la plataforma) y sobrescribe siempre; `X-Forwarded-For` se ignora porque el proxy de la plataforma conserva lo que escribe el cliente. | `test_main_starts_https_and_the_redirect`, `test_the_real_ip_comes_from_the_proxy_header`, `test_without_a_valid_proxy_header_nothing_is_trusted` y el job «Docker» de la CI |
| Cabeceras de descarga | Los nombres de fichero se reducen a ASCII seguro en `Content-Disposition`. | `test_card_filename_is_safe` |
| XSS en la web | <ul><li>CSP propia (`script-src 'self'`, sin `unsafe-inline` ni CDN).</li><li>El HTML no tiene código en línea.</li><li>Todo el texto (apodos, notas, nombres) se pinta con `textContent`: el JavaScript no usa `innerHTML`, `eval` ni `document.write`.</li></ul> | `test_index_is_served_with_its_own_strict_csp`, `test_index_has_no_inline_code`, `test_javascript_never_injects_html_or_evaluates_code` |
| *Cross-Site WebSocket Hijacking* y abuso del combate en directo | <ul><li>El WebSocket solo se acepta si el `Origin` es el propio sitio: una web ajena no puede abrirlo con tu sesión.</li><li>El token va en el **primer mensaje**, nunca en la URL (que acabaría en logs e historiales), y hay 10 segundos para enviarlo.</li><li>El servidor decide cada turno: el navegador solo propone acciones, que el motor valida.</li><li>Como mucho 120 mensajes por minuto por conexión y un reto en espera por usuario; los códigos (32<sup>6</sup> combinaciones, generados con `secrets`) caducan a los 15 minutos.</li><li>Solo se combate con equipos propios, y en cada reto caben dos entrenadores.</li></ul> | `test_other_sites_cannot_open_the_socket`, `test_invalid_token_is_rejected`, `test_guest_must_bring_their_own_team`, `test_a_full_room_rejects_a_third_trainer`, `test_invalid_actions_are_refused`, `test_room_codes_expire` |
| Robo de tokens en la web | <ul><li>El access token vive **solo en memoria**.</li><li>El refresh token de los navegadores va en una cookie **`HttpOnly`** (ningún script puede leerla), `Secure` por HTTPS, `SameSite=Strict` y limitada a `/api/v1/auth`. Rota en cada uso.</li><li>Varias peticiones con 401, también desde varias pestañas (Web Locks), comparten una única renovación, para no disparar la detección de reutilización.</li><li>Los tokens de Google llegan en el fragmento de la URL, que no se envía a ningún servidor, y se borran del historial al leerlos.</li></ul> | `tests/web/api.test.mjs`, `test_reusing_a_rotated_cookie_revokes_the_session` |
| CSRF y *login CSRF* con la cookie de sesión | La cookie solo se emite y se acepta si la petición viene del propio sitio (cabeceras `Origin` o `Sec-Fetch-Site`), además de `SameSite=Strict`. Una web ajena no puede renovar tu sesión ni dejarte con la de otro. Los clientes de la API, que no envían esas cabeceras, siguen usando el refresh token del cuerpo. | `test_the_cookie_is_ignored_from_another_site`, `test_another_site_cannot_log_you_in`, `test_api_clients_get_no_cookie` |

### Cadena de suministro

- **Versiones exactas en `uv.lock`.** La CI falla si `uv.lock`, `pyproject.toml` y `requirements.txt` no coinciden, y la imagen de Docker se construye con `uv sync --locked`.
- **Sin etiquetas que se puedan mover:** las acciones de GitHub se fijan por **SHA** y las imágenes de Docker por **digest**.
- **Dependabot** propone actualizaciones semanales de dependencias, acciones e imágenes, siempre como PR contra `develop` y con CI.
- **Mínimo privilegio en la CI:** el flujo solo tiene permisos de lectura.
- **`pip-audit`** revisa las vulnerabilidades conocidas en cada PR.

### Riesgos aceptados

| Aviso | Paquete | Por qué no afecta |
|---|---|---|
| [GHSA-3qf3-8w2g-rqmx](https://github.com/advisories/GHSA-3qf3-8w2g-rqmx) / PYSEC-2026-4298, crítico | `python-jose` | Confusión de algoritmo: el atacante consigue que una clave pública se use como secreto HMAC. Exige que el servidor acepte el algoritmo que indica el token, y aquí nunca lo hace: los tokens propios se verifican solo con `HS256` y un secreto simétrico, y los ID tokens de Google solo con `RS256`. Hay tests que intentan el ataque. |
| [GHSA-wj6h-64fc-37mp](https://github.com/advisories/GHSA-wj6h-64fc-37mp) / PYSEC-2026-1325, alto | `ecdsa` | Ataque de canal lateral (Minerva) al **firmar** con ECDSA P-256. La API nunca firma con ECDSA: firma con HMAC-SHA256. |

Ambos se ignoran en `pip-audit` de forma explícita, con esta justificación en [`ci.yml`](.github/workflows/ci.yml). `python-jose` es obligatorio según el enunciado de la práctica; en un proyecto real se sustituiría por `PyJWT` o `joserfc`, con lo que desaparecerían los dos avisos.

### Limitaciones conocidas

Se documentan para que nadie dé por hecha una protección que no existe:

- **Logout:** revoca el refresh token y toda su familia de sesión, pero el access token sigue siendo válido hasta que caduca (24 horas, como pide el enunciado). Lo mitigan dos cosas:
  - el usuario se vuelve a comprobar en cada petición, así que desactivar una cuenta corta su acceso al momento;
  - el token solo sirve para la propia cuenta.

  Mejora prevista: access tokens de 15 minutos y una lista de `jti` revocados.
- **Retos en memoria:** las salas del combate en directo viven en el proceso; si el servidor se reinicia, los combates en curso se pierden (los terminados ya están en la base de datos).
- **Rate limiting en memoria:** con varias réplicas, cada una cuenta por separado. Para compartir los contadores basta con instalar `redis` y poner `RATE_LIMIT_STORAGE_URI=redis://…`.
- **Emails sin verificar en las cuentas locales:** por eso el login con Google nunca vincula cuentas por email.
- **SQLite:** adecuada para un único servidor. Para varios, `DATABASE_URL` admite PostgreSQL instalando su driver (`psycopg`), sin cambiar el código.
- **Certificado local:** el navegador avisa hasta que se confía en `certs/ca.pem`. En un dominio público, Caddy obtiene un certificado de Let's Encrypt.

## Informar de una vulnerabilidad

- **No abras un *issue***: lo verían todos los que tienen acceso al repositorio antes de que exista una corrección.
- Escribe al autor por el canal privado de la asignatura, con:
  - los pasos para reproducirlo;
  - el impacto que crees que tiene;
  - la versión o el commit afectados.
- La corrección se hará en una rama `fix/` con su test, siguiendo el flujo normal de PR y CI.

| Versión | Soporte |
|---|---|
| Última de `main` | ✅ |
| Anteriores | ❌ |
