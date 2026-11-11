"""Servidor HTTPS de ``python -m app``: certificado local, redirección y arranque real."""

import asyncio
import ipaddress
import socket
import ssl
import threading
import time
from datetime import timedelta
from pathlib import Path

import httpx
import pytest
from cryptography import x509
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import ExtendedKeyUsageOID
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app import server
from app.config import Settings, get_settings
from app.server import (
    CA_FILE,
    CERT_FILE,
    KEY_FILE,
    TLSFiles,
    bind_socket,
    build_redirect_app,
    ensure_tls,
    generate_local_certificate,
)


def settings_with(**overrides: object) -> Settings:
    return get_settings().model_copy(update=overrides)


def load(path: Path) -> x509.Certificate:
    return x509.load_pem_x509_certificate(path.read_bytes())


# ═════════════════════════════════════════════════════════════
#  Certificado local
# ═════════════════════════════════════════════════════════════
def test_local_certificate_is_signed_by_a_constrained_ca(tmp_path: Path) -> None:
    files = generate_local_certificate(tmp_path)

    assert sorted(p.name for p in tmp_path.iterdir()) == sorted([CA_FILE, CERT_FILE, KEY_FILE])
    ca, cert = load(tmp_path / CA_FILE), load(tmp_path / CERT_FILE)

    # La CA solo puede firmar para localhost: ni otros dominios ni CA intermedias
    assert ca.extensions.get_extension_for_class(x509.BasicConstraints).value.path_length == 0
    constraints = ca.extensions.get_extension_for_class(x509.NameConstraints)
    assert constraints.critical
    assert set(constraints.value.permitted_subtrees) == {
        x509.DNSName("localhost"),
        x509.IPAddress(ipaddress.ip_network("127.0.0.1/32")),
        x509.IPAddress(ipaddress.ip_network("::1/128")),
    }

    # El certificado de servidor está firmado por la CA y es solo para TLS de servidor
    cert.verify_directly_issued_by(ca)
    san = cert.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
    assert san.get_values_for_type(x509.DNSName) == ["localhost"]
    assert {str(ip) for ip in san.get_values_for_type(x509.IPAddress)} == {"127.0.0.1", "::1"}
    eku = cert.extensions.get_extension_for_class(x509.ExtendedKeyUsage).value
    assert list(eku) == [ExtendedKeyUsageOID.SERVER_AUTH]
    assert not cert.extensions.get_extension_for_class(x509.BasicConstraints).value.ca
    assert isinstance(cert.public_key(), ec.EllipticCurvePublicKey)
    assert files.ca == tmp_path / CA_FILE


def test_the_ca_private_key_is_never_written(tmp_path: Path) -> None:
    generate_local_certificate(tmp_path)

    keys = [p.name for p in tmp_path.iterdir() if b"PRIVATE KEY" in p.read_bytes()]
    assert keys == [KEY_FILE]  # solo la del servidor, no la de la CA


def test_ensure_tls_reuses_a_valid_certificate(tmp_path: Path) -> None:
    settings = settings_with(tls_cert_dir=str(tmp_path))
    first = ensure_tls(settings)
    serial = load(first.certfile).serial_number

    assert load(ensure_tls(settings).certfile).serial_number == serial


def test_ensure_tls_renews_a_certificate_about_to_expire(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = settings_with(tls_cert_dir=str(tmp_path))
    serial = load(ensure_tls(settings).certfile).serial_number
    monkeypatch.setattr(server, "RENEW_BEFORE", timedelta(days=400))

    assert load(ensure_tls(settings).certfile).serial_number != serial


def test_ensure_tls_regenerates_when_a_file_is_missing(tmp_path: Path) -> None:
    settings = settings_with(tls_cert_dir=str(tmp_path))
    ensure_tls(settings)
    (tmp_path / KEY_FILE).unlink()

    assert ensure_tls(settings).keyfile.is_file()


def test_ensure_tls_uses_a_custom_certificate(tmp_path: Path) -> None:
    own = generate_local_certificate(tmp_path / "own")
    settings = settings_with(tls_certfile=str(own.certfile), tls_keyfile=str(own.keyfile))

    assert ensure_tls(settings) == TLSFiles(own.certfile, own.keyfile, None)


def test_ensure_tls_rejects_a_missing_custom_certificate(tmp_path: Path) -> None:
    settings = settings_with(tls_certfile=str(tmp_path / "x.pem"), tls_keyfile="y.pem")

    with pytest.raises(SystemExit, match="No existe el fichero TLS"):
        ensure_tls(settings)


@pytest.mark.parametrize(
    "env",
    [
        {"TLS_CERTFILE": "cert.pem"},  # falta la clave
        {"HTTPS_PORT": "8443", "HTTP_REDIRECT_PORT": "8443"},
    ],
)
def test_invalid_server_settings(monkeypatch: pytest.MonkeyPatch, env: dict[str, str]) -> None:
    for name, value in env.items():
        monkeypatch.setenv(name, value)

    with pytest.raises(ValidationError):
        Settings()


# ═════════════════════════════════════════════════════════════
#  Redirección HTTP → HTTPS
# ═════════════════════════════════════════════════════════════
def redirect_client(**overrides: object) -> TestClient:
    app = build_redirect_app(settings_with(**overrides))
    return TestClient(app, base_url="http://localhost", follow_redirects=False)


def test_http_redirects_to_https_keeping_path_and_query() -> None:
    response = redirect_client().post("/api/v1/pokemon/search?q=pika%20chu&limit=5")

    assert response.status_code == 308  # conserva el método y el cuerpo, a diferencia de 301
    assert (
        response.headers["location"]
        == "https://localhost/api/v1/pokemon/search?q=pika%20chu&limit=5"
    )
    assert "server" not in response.headers


def test_redirect_includes_a_non_default_https_port() -> None:
    response = redirect_client(https_port=8443).get("/docs", headers={"Host": "127.0.0.1:8080"})

    assert response.headers["location"] == "https://127.0.0.1:8443/docs"


def test_redirect_supports_ipv6_hosts() -> None:
    response = redirect_client(allowed_hosts=["[::1]"]).get("/", headers={"Host": "[::1]:80"})

    assert response.headers["location"] == "https://[::1]/"


def test_redirect_supports_wildcard_hosts() -> None:
    client = redirect_client(allowed_hosts=["*.pokedex.test"])

    assert client.get("/", headers={"Host": "api.pokedex.test"}).status_code == 308


@pytest.mark.parametrize("host", ["evil.example", "localhost.evil.example", "", "[::1"])
def test_redirect_refuses_unknown_hosts(host: str) -> None:
    """Si redirigiera a cualquier Host, sería una redirección abierta."""
    response = redirect_client().get("/", headers={"Host": host})

    assert response.status_code == 400
    assert "location" not in response.headers


def test_redirect_app_ignores_lifespan_events() -> None:
    sent: list[object] = []

    async def send(message: object) -> None:
        sent.append(message)

    async def receive() -> dict[str, str]:
        return {"type": "lifespan.startup"}

    app = build_redirect_app(get_settings())
    asyncio.run(app({"type": "lifespan"}, receive, send))  # type: ignore[arg-type]

    assert sent == []


# ═════════════════════════════════════════════════════════════
#  Arranque real
# ═════════════════════════════════════════════════════════════
def free_socket() -> socket.socket:
    return bind_socket("127.0.0.1", 0)


def test_bind_socket_fails_on_a_busy_port() -> None:
    busy = free_socket()
    busy.listen()
    try:
        with pytest.raises(OSError):
            bind_socket("127.0.0.1", busy.getsockname()[1])
    finally:
        busy.close()


def test_api_is_served_over_tls_with_a_redirect(tmp_path: Path) -> None:
    """Arranca de verdad los dos servidores y comprueba el handshake TLS validando la
    cadena contra la CA local, como haría un cliente que confía en ella."""
    settings = settings_with(tls_cert_dir=str(tmp_path))
    tls = ensure_tls(settings)
    https_sock, redirect_sock = free_socket(), free_socket()
    https_port = https_sock.getsockname()[1]
    https, redirect = server.https_server(settings, tls), server.redirect_server(settings)
    thread = threading.Thread(
        target=asyncio.run, args=(server.serve(https, https_sock, redirect, redirect_sock),)
    )
    thread.start()
    try:
        deadline = time.monotonic() + 15
        while not (https.started and redirect.started):
            assert time.monotonic() < deadline, "el servidor no ha arrancado"
            time.sleep(0.05)

        context = ssl.create_default_context(cafile=str(tls.ca))
        response = httpx.get(f"https://127.0.0.1:{https_port}/health", verify=context)
        assert response.status_code == 200
        assert response.http_version == "HTTP/1.1"
        assert "server" not in response.headers
        assert "strict-transport-security" in response.headers  # HSTS solo por HTTPS

        with pytest.raises(httpx.ConnectError):  # sin la CA, el certificado no es de confianza
            httpx.get(f"https://127.0.0.1:{https_port}/health")

        redirect_port = redirect_sock.getsockname()[1]
        plain = httpx.get(f"http://127.0.0.1:{redirect_port}/health?x=1")
        assert plain.status_code == 308
        assert plain.headers["location"] == "https://127.0.0.1/health?x=1"
    finally:
        https.should_exit = True
        thread.join(timeout=15)
    assert not thread.is_alive()
    assert redirect.should_exit  # el secundario se detiene con el principal


# ═════════════════════════════════════════════════════════════
#  main()
# ═════════════════════════════════════════════════════════════
@pytest.fixture
def fake_run(monkeypatch: pytest.MonkeyPatch) -> list[tuple]:
    """Sustituye el bucle real: registra con qué servidores y sockets arrancaría."""
    calls: list[tuple] = []

    def run(coro: object) -> None:
        frame = coro.cr_frame  # type: ignore[attr-defined]
        calls.append(tuple(frame.f_locals[k] for k in ("https", "redirect", "redirect_sock")))
        coro.close()  # type: ignore[attr-defined]
        for sock in (frame.f_locals["https_sock"], frame.f_locals["redirect_sock"]):
            if sock is not None:
                sock.close()
        raise KeyboardInterrupt  # Ctrl+C: main() sale sin traza

    monkeypatch.setattr(server.asyncio, "run", run)
    return calls


def patch_settings(monkeypatch: pytest.MonkeyPatch, **overrides: object) -> None:
    settings = settings_with(server_host="127.0.0.1", https_port=0, **overrides)
    monkeypatch.setattr(server, "get_settings", lambda: settings)


def test_main_starts_https_and_the_redirect(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fake_run: list, capsys
) -> None:
    patch_settings(monkeypatch, tls_cert_dir=str(tmp_path), http_redirect_port=8080)
    monkeypatch.setattr(server, "bind_socket", lambda host, port: free_socket())

    server.main()

    ((https, redirect, redirect_sock),) = fake_run
    assert https.config.ssl_certfile == str(tmp_path / CERT_FILE)
    assert https.config.server_header is False
    assert https.config.proxy_headers is False
    assert redirect is not None and redirect_sock is not None
    err = capsys.readouterr().err
    assert "redirige a HTTPS" in err
    assert f"--cacert {tmp_path / CA_FILE}" in err


def test_main_without_redirect_port(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fake_run: list
) -> None:
    patch_settings(monkeypatch, tls_cert_dir=str(tmp_path), http_redirect_port=0)

    server.main()

    assert fake_run[0][1:] == (None, None)


def test_main_keeps_https_if_port_80_is_busy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fake_run: list, capsys
) -> None:
    patch_settings(monkeypatch, tls_cert_dir=str(tmp_path), http_redirect_port=8080)
    real_bind = server.bind_socket

    def bind(host: str, port: int) -> socket.socket:
        if port == 8080:
            raise OSError(98, "Address already in use")
        return real_bind(host, port)

    monkeypatch.setattr(server, "bind_socket", bind)

    server.main()

    assert fake_run[0][1:] == (None, None)
    assert "sin redirección desde HTTP" in capsys.readouterr().err


def test_main_explains_why_port_443_failed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    patch_settings(monkeypatch, tls_cert_dir=str(tmp_path))

    def bind(host: str, port: int) -> socket.socket:
        raise PermissionError(13, "Permission denied")

    monkeypatch.setattr(server, "bind_socket", bind)

    with pytest.raises(SystemExit, match="HTTPS_PORT=8443"):
        server.main()


def test_public_url_hides_the_default_port() -> None:
    assert server._public_url(settings_with(https_port=443)) == "https://localhost"
    assert server._public_url(settings_with(https_port=8443)) == "https://localhost:8443"


def test_certificate_validity_is_short() -> None:
    """Un año como máximo: los navegadores rechazan certificados de más de 398 días."""
    assert server.CERT_VALIDITY.days <= 398
