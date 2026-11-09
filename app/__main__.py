"""``uv run python -m app``: la API por HTTPS en el puerto 443 (ver ``app/server.py``)."""

from app.server import main

if __name__ == "__main__":  # pragma: no cover
    main()
