"""Publica la Pokédex en Internet con un túnel rápido de Cloudflare: ``python -m app.tunnel``.

Gratis y sin cuenta. ``cloudflared`` abre una conexión de salida hacia Cloudflare, que
da una dirección pública ``https://….trycloudflare.com``. No hay que abrir puertos del
router ni exponer el PC: el servidor sigue escuchando solo en esta máquina.

Lo que hace este lanzador, solo para esta ejecución (no toca ``.env``):

1. Arranca ``cloudflared`` contra el servidor HTTPS local y espera a que dé la dirección.
   Comprueba el certificado del servidor con la CA local (``certs/ca.pem``).
2. Añade esa dirección a ``ALLOWED_HOSTS`` y la usa en ``GOOGLE_REDIRECT_URI``.
3. Activa ``TRUST_CLOUDFLARE_TUNNEL`` para que los límites de intentos usen la IP real
   de cada visitante y no la de ``cloudflared``.
4. Arranca el servidor HTTPS (``python -m app``) y, al pararlo, cierra el túnel.

Limitaciones del túnel rápido: la dirección cambia en cada ejecución y la web solo se ve
mientras el PC y este proceso estén en marcha.
"""

import os
import queue
import re
import shutil
import subprocess
import sys
import threading
from pathlib import Path
from urllib.parse import urlsplit

from app.config import get_settings
from app.server import ensure_tls
from app.server import main as serve_https

TUNNEL_URL = re.compile(r"https://[a-z0-9-]+\.trycloudflare\.com")
WAIT_SECONDS = 60
WINDOWS_PATHS = (
    Path(os.environ.get("PROGRAMFILES(X86)", r"C:\Program Files (x86)"))
    / "cloudflared"
    / "cloudflared.exe",
    Path(os.environ.get("PROGRAMFILES", r"C:\Program Files")) / "cloudflared" / "cloudflared.exe",
)


def find_cloudflared() -> str:
    found = shutil.which("cloudflared")
    if found:
        return found
    for path in WINDOWS_PATHS:
        if path.exists():
            return str(path)
    raise SystemExit(
        "No encuentro cloudflared. Instálalo con «winget install Cloudflare.cloudflared» "
        "(Windows) o desde https://developers.cloudflare.com/cloudflare-one/connections/"
        "connect-networks/downloads/"
    )


def tunnel_command(cloudflared: str, port: int, ca: Path | None) -> list[str]:
    command = [cloudflared, "tunnel", "--no-autoupdate", "--url", f"https://localhost:{port}"]
    # El tramo cloudflared → servidor también va cifrado, y se comprueba el certificado
    return command + (["--origin-ca-pool", str(ca)] if ca else ["--no-tls-verify"])


def session_environment(url: str, allowed_hosts: list[str]) -> dict[str, str]:
    """Variables que esta ejecución necesita para servir en la dirección del túnel."""
    host = urlsplit(url).hostname or ""
    return {
        "ALLOWED_HOSTS": ",".join(dict.fromkeys([*allowed_hosts, host])),
        "GOOGLE_REDIRECT_URI": f"{url}/api/v1/auth/google/callback",
        "TRUST_CLOUDFLARE_TUNNEL": "true",
    }


def wait_for_url(process: subprocess.Popen[str]) -> str:
    """Lee la salida de cloudflared (en otro hilo, para que nunca se llene la tubería)
    hasta que aparece la dirección pública."""
    lines: queue.Queue[str] = queue.Queue()

    def pump() -> None:
        assert process.stderr is not None  # noqa: S101  (se abre con stderr=PIPE)
        for line in process.stderr:
            lines.put(line)
            if " ERR " in line:
                print(f"[cloudflared] {line.rstrip()}", file=sys.stderr)

    threading.Thread(target=pump, daemon=True).start()
    while True:
        try:
            line = lines.get(timeout=WAIT_SECONDS)
        except queue.Empty:
            raise SystemExit("cloudflared no ha dado una dirección a tiempo.") from None
        if match := TUNNEL_URL.search(line):
            return match.group(0)


def main() -> None:
    settings = get_settings()
    tls = ensure_tls(settings)
    command = tunnel_command(find_cloudflared(), settings.https_port, tls.ca)
    print("Abriendo el túnel de Cloudflare…", file=sys.stderr)
    process = subprocess.Popen(  # noqa: S603  (ruta y argumentos fijados aquí)
        command, stderr=subprocess.PIPE, stdout=subprocess.DEVNULL, text=True, encoding="utf-8"
    )
    try:
        url = wait_for_url(process)
        os.environ.update(session_environment(url, settings.allowed_hosts))
        get_settings.cache_clear()
        redirect = os.environ["GOOGLE_REDIRECT_URI"]
        print(
            "\n".join(
                [
                    "",
                    "═" * 72,
                    f"  Pokédex publicada en:  {url}",
                    "",
                    "  Para entrar con Google, añade esta URI en Google Cloud Console",
                    "  (APIs y servicios → Credenciales → tu cliente OAuth →",
                    "  URIs de redireccionamiento autorizados):",
                    f"    {redirect}",
                    "",
                    "  La dirección cambia cada vez que se abre el túnel. Para pararlo: Ctrl+C",
                    "═" * 72,
                    "",
                ]
            ),
            file=sys.stderr,
        )
        serve_https()
    finally:
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()


if __name__ == "__main__":
    main()
