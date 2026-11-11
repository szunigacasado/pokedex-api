"""Publicación con un túnel de Cloudflare: IP real de confianza y configuración de la sesión."""

import io
from pathlib import Path

import pytest

from app import tunnel
from app.security_middleware import CloudflareTunnelMiddleware


async def client_seen_by_the_app(client: tuple[str, int], headers: list[tuple[bytes, bytes]]):
    seen = {}

    async def app(scope, receive, send):
        seen["client"] = scope["client"]

    await CloudflareTunnelMiddleware(app)(
        {"type": "http", "client": client, "headers": headers}, None, None
    )
    return seen["client"][0]


@pytest.mark.parametrize(
    ("client", "header", "expected"),
    [
        (("127.0.0.1", 5000), b"203.0.113.9", "203.0.113.9"),  # viene del túnel
        (("::1", 5000), b"2001:db8::1", "2001:db8::1"),
        (("198.51.100.7", 5000), b"203.0.113.9", "198.51.100.7"),  # desde fuera: se ignora
        (("127.0.0.1", 5000), b"no-es-una-ip", "127.0.0.1"),
        (("127.0.0.1", 5000), None, "127.0.0.1"),
    ],
)
async def test_real_ip_is_only_trusted_from_the_local_tunnel(client, header, expected) -> None:
    headers = [(b"cf-connecting-ip", header)] if header else []
    assert await client_seen_by_the_app(client, headers) == expected


def test_the_middleware_is_off_by_default() -> None:
    from app.main import app

    assert all(m.cls is not CloudflareTunnelMiddleware for m in app.user_middleware)


def test_session_environment() -> None:
    env = tunnel.session_environment(
        "https://abc-def.trycloudflare.com", ["localhost", "127.0.0.1"]
    )
    assert env == {
        "ALLOWED_HOSTS": "localhost,127.0.0.1,abc-def.trycloudflare.com",
        "GOOGLE_REDIRECT_URI": "https://abc-def.trycloudflare.com/api/v1/auth/google/callback",
        "TRUST_CLOUDFLARE_TUNNEL": "true",
    }


def test_the_tunnel_checks_the_local_certificate() -> None:
    command = tunnel.tunnel_command("cloudflared", 443, Path("certs/ca.pem"))
    assert command[-2:] == ["--origin-ca-pool", str(Path("certs/ca.pem"))]
    assert "--no-tls-verify" not in command
    assert "https://localhost:443" in command


def test_wait_for_url_reads_the_cloudflared_output() -> None:
    class FakeProcess:
        stderr = io.StringIO(
            "2026-11-11T10:00:00Z INF Requesting new quick Tunnel on trycloudflare.com...\n"
            "2026-11-11T10:00:01Z INF |  https://brave-otter-42.trycloudflare.com  |\n"
        )

    assert tunnel.wait_for_url(FakeProcess()) == "https://brave-otter-42.trycloudflare.com"
