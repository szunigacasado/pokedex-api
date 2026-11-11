"""Servidor HTTPS en el puerto 443 sin herramientas externas: ``uv run python -m app``.

- Si no hay certificado, genera una CA local y un certificado para ``localhost`` firmado
  por ella. La clave privada de la CA **no se guarda nunca** y la CA lleva *Name
  Constraints*: aunque se marque como de confianza en el sistema, no puede firmar nada
  más ni servir para otro dominio que ``localhost``, ``127.0.0.1`` y ``::1``.
- Escucha HTTPS en ``HTTPS_PORT`` (443) y, si el puerto está libre, redirige HTTP
  (``HTTP_REDIRECT_PORT``, 80) a HTTPS con un 308.
- No anuncia el servidor (sin cabecera ``Server``) y no confía en ``X-Forwarded-*``: sin
  un proxy delante, esas cabeceras las escribe el cliente, que podría falsear su IP para
  saltarse el rate limiting o el bloqueo de cuenta. (Uvicorn, por defecto, sí las
  acepta de 127.0.0.1.)
"""

import asyncio
import contextlib
import ipaddress
import os
import socket
import sys
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

import uvicorn
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID
from starlette.types import ASGIApp, Receive, Scope, Send

from app.config import Settings, get_settings

LOCAL_DNS_NAMES: tuple[str, ...] = ("localhost",)
LOCAL_IPS: tuple[str, ...] = ("127.0.0.1", "::1")
CA_FILE = "ca.pem"
CERT_FILE = "localhost.pem"
KEY_FILE = "localhost-key.pem"
CERT_VALIDITY = timedelta(days=365)
RENEW_BEFORE = timedelta(days=7)


# ═════════════════════════════════════════════════════════════
#  Certificado local
# ═════════════════════════════════════════════════════════════
@dataclass(frozen=True)
class TLSFiles:
    certfile: Path
    keyfile: Path
    ca: Path | None  # CA local en la que puede confiar el cliente; None si es un cert propio


def _local_names() -> list[x509.GeneralName]:
    names: list[x509.GeneralName] = [x509.DNSName(name) for name in LOCAL_DNS_NAMES]
    return names + [x509.IPAddress(ipaddress.ip_address(ip)) for ip in LOCAL_IPS]


def generate_local_certificate(directory: Path) -> TLSFiles:
    """Crea ``ca.pem``, ``localhost.pem`` y ``localhost-key.pem`` en ``directory``."""
    directory.mkdir(parents=True, exist_ok=True)
    now = datetime.now(UTC)
    not_before, not_after = now - timedelta(minutes=5), now + CERT_VALIDITY

    ca_key = ec.generate_private_key(ec.SECP256R1())
    ca_name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Pokedex API local CA")])
    ca_ski = x509.SubjectKeyIdentifier.from_public_key(ca_key.public_key())
    permitted: list[x509.GeneralName] = [x509.DNSName(name) for name in LOCAL_DNS_NAMES]
    permitted += [x509.IPAddress(ipaddress.ip_network(ip)) for ip in LOCAL_IPS]
    ca_cert = (
        x509.CertificateBuilder()
        .subject_name(ca_name)
        .issuer_name(ca_name)
        .public_key(ca_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(not_before)
        .not_valid_after(not_after)
        .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
        .add_extension(_key_usage(cert_sign=True), critical=True)
        .add_extension(x509.NameConstraints(permitted, None), critical=True)
        .add_extension(ca_ski, critical=False)
        .sign(ca_key, hashes.SHA256())
    )

    key = ec.generate_private_key(ec.SECP256R1())
    cert = (
        x509.CertificateBuilder()
        .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "localhost")]))
        .issuer_name(ca_name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(not_before)
        .not_valid_after(not_after)
        .add_extension(x509.SubjectAlternativeName(_local_names()), critical=False)
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .add_extension(_key_usage(cert_sign=False), critical=True)
        .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), critical=False)
        .add_extension(x509.SubjectKeyIdentifier.from_public_key(key.public_key()), critical=False)
        .add_extension(
            x509.AuthorityKeyIdentifier.from_issuer_subject_key_identifier(ca_ski),
            critical=False,
        )
        .sign(ca_key, hashes.SHA256())
    )
    # ca_key sale de ámbito aquí: nunca se escribe en disco.

    ca_path = directory / CA_FILE
    files = TLSFiles(directory / CERT_FILE, directory / KEY_FILE, ca_path)
    ca_path.write_bytes(ca_cert.public_bytes(serialization.Encoding.PEM))
    files.certfile.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    key_pem = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )
    _write_private(files.keyfile, key_pem)
    return files


def _key_usage(*, cert_sign: bool) -> x509.KeyUsage:
    return x509.KeyUsage(
        digital_signature=not cert_sign,
        content_commitment=False,
        key_encipherment=False,
        data_encipherment=False,
        key_agreement=False,
        key_cert_sign=cert_sign,
        crl_sign=cert_sign,
        encipher_only=False,
        decipher_only=False,
    )


def _write_private(path: Path, data: bytes) -> None:
    """Escribe la clave privada legible solo por el usuario actual (0600 en Unix)."""
    path.unlink(missing_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as file:
        file.write(data)


def _needs_renewal(certfile: Path) -> bool:
    cert = x509.load_pem_x509_certificate(certfile.read_bytes())
    return cert.not_valid_after_utc - datetime.now(UTC) < RENEW_BEFORE


def ensure_tls(settings: Settings) -> TLSFiles:
    """Certificado propio si se ha configurado; si no, el local (generándolo si falta o
    está a punto de caducar)."""
    if settings.tls_certfile:
        files = TLSFiles(Path(settings.tls_certfile), Path(settings.tls_keyfile), None)
        for path in (files.certfile, files.keyfile):
            if not path.is_file():
                raise SystemExit(f"No existe el fichero TLS {path}")
        return files

    directory = Path(settings.tls_cert_dir)
    files = TLSFiles(directory / CERT_FILE, directory / KEY_FILE, directory / CA_FILE)
    paths = (files.certfile, files.keyfile, files.ca)
    if all(p.is_file() for p in paths if p) and not _needs_renewal(files.certfile):
        return files
    return generate_local_certificate(directory)


# ═════════════════════════════════════════════════════════════
#  Redirección HTTP → HTTPS
# ═════════════════════════════════════════════════════════════
def _host_without_port(value: str) -> str | None:
    value = value.strip().lower()
    if value.startswith("["):  # IPv6: [::1]:80
        end = value.find("]")
        return value[: end + 1] if end > 0 else None
    return value.split(":", 1)[0] or None


def _host_allowed(host: str, allowed: list[str]) -> bool:
    """Mismas reglas que ``TrustedHostMiddleware``: exacto, ``*`` o ``*.dominio``."""
    return any(
        pattern == "*"
        or host == pattern
        or (pattern.startswith("*.") and host.endswith(pattern[1:]))
        for pattern in allowed
    )


def build_redirect_app(settings: Settings) -> ASGIApp:
    """App ASGI mínima que responde 308 hacia la misma URL en HTTPS.

    Solo redirige a hosts de ``ALLOWED_HOSTS``: con cualquier otro responde 400, para no
    convertirse en una redirección abierta hacia un dominio que escriba el cliente.
    """
    allowed = settings.allowed_hosts
    port = "" if settings.https_port == 443 else f":{settings.https_port}"

    async def redirect(scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            return
        headers = dict(scope["headers"])
        host = _host_without_port(headers.get(b"host", b"").decode("latin-1"))
        if host is None or not _host_allowed(host, allowed):
            await _respond(send, 400, [])
            return
        path = scope.get("raw_path") or scope["path"].encode()
        query = scope.get("query_string", b"")
        location = f"https://{host}{port}".encode() + path + (b"?" + query if query else b"")
        await _respond(send, 308, [(b"location", location)])

    return redirect


async def _respond(send: Send, status: int, headers: list[tuple[bytes, bytes]]) -> None:
    headers = [*headers, (b"content-length", b"0")]
    await send({"type": "http.response.start", "status": status, "headers": headers})
    await send({"type": "http.response.body", "body": b""})


# ═════════════════════════════════════════════════════════════
#  Arranque
# ═════════════════════════════════════════════════════════════
class _SecondaryServer(uvicorn.Server):
    """Servidor de la redirección: no captura señales, se detiene con el principal."""

    @contextlib.contextmanager
    def capture_signals(self) -> Iterator[None]:
        yield


def bind_socket(host: str, port: int) -> socket.socket:
    family = socket.AF_INET6 if ":" in host else socket.AF_INET
    sock = socket.socket(family, socket.SOCK_STREAM)
    if os.name == "nt":
        # En Windows, SO_REUSEADDR permitiría que otro proceso «robe» el puerto; con
        # SO_EXCLUSIVEADDRUSE nadie más puede escuchar en él mientras esté abierto.
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
    else:  # pragma: no cover  (la CI corre en Linux; la cobertura se mide igual)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        sock.bind((host, port))
    except OSError:
        sock.close()
        raise
    return sock


def https_server(settings: Settings, tls: TLSFiles) -> uvicorn.Server:
    return uvicorn.Server(
        uvicorn.Config(
            "app.main:app",
            ssl_certfile=str(tls.certfile),
            ssl_keyfile=str(tls.keyfile),
            server_header=False,
            access_log=False,  # la API ya registra cada petición con su X-Request-ID
            proxy_headers=False,
            log_level=settings.log_level.lower(),
        )
    )


def redirect_server(settings: Settings) -> uvicorn.Server:
    return _SecondaryServer(
        uvicorn.Config(
            build_redirect_app(settings),
            lifespan="off",
            server_header=False,
            access_log=False,
            proxy_headers=False,
            log_level="warning",
        )
    )


async def serve(
    https: uvicorn.Server,
    https_sock: socket.socket,
    redirect: uvicorn.Server | None = None,
    redirect_sock: socket.socket | None = None,
) -> None:
    task = None
    if redirect is not None and redirect_sock is not None:
        task = asyncio.create_task(redirect.serve(sockets=[redirect_sock]))
    try:
        await https.serve(sockets=[https_sock])
    finally:
        if task is not None and redirect is not None:
            redirect.should_exit = True
            await task


def _public_url(settings: Settings) -> str:
    port = "" if settings.https_port == 443 else f":{settings.https_port}"
    return f"https://localhost{port}"


def main() -> None:
    settings = get_settings()
    tls = ensure_tls(settings)
    try:
        https_sock = bind_socket(settings.server_host, settings.https_port)
    except OSError as exc:
        raise SystemExit(
            f"No se puede escuchar en {settings.server_host}:{settings.https_port} "
            f"({exc.strerror}). Si el puerto está ocupado, para el otro proceso; en Linux, "
            "los puertos menores de 1024 requieren privilegios: usa HTTPS_PORT=8443 o "
            "Docker (docker compose up)."
        ) from exc

    redirect_sock = None
    if settings.http_redirect_port:
        try:
            redirect_sock = bind_socket(settings.server_host, settings.http_redirect_port)
        except OSError as exc:
            print(
                f"Aviso: el puerto {settings.http_redirect_port} no está disponible "
                f"({exc.strerror}); se sirve solo HTTPS, sin redirección desde HTTP.",
                file=sys.stderr,
            )

    url = _public_url(settings)
    lines = [f"Pokédex API en {url}  (documentación: {url}/docs)"]
    if redirect_sock is not None:
        lines.append(f"HTTP en el puerto {settings.http_redirect_port} redirige a HTTPS")
    if tls.ca is not None:
        lines.append(f"Certificado local firmado por la CA {tls.ca}. Para que curl confíe en él:")
        # El curl de Windows (Schannel) exige comprobar la revocación, y un certificado
        # local no publica lista de revocación.
        no_revoke = "--ssl-no-revoke " if os.name == "nt" else ""
        lines.append(f"  curl {no_revoke}--cacert {tls.ca} {url}/health")
    print("\n".join(lines), file=sys.stderr)

    redirect = redirect_server(settings) if redirect_sock is not None else None
    with contextlib.suppress(KeyboardInterrupt):
        asyncio.run(serve(https_server(settings, tls), https_sock, redirect, redirect_sock))
