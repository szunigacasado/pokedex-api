"""Piezas de resiliencia para hablar con servicios externos: caché y *circuit breaker*.

Ambas reciben el reloj como parámetro (``clock``) para que los tests puedan avanzar el
tiempo sin esperar de verdad.
"""

import time
from collections import OrderedDict
from collections.abc import Callable
from typing import Literal

Clock = Callable[[], float]


class TTLCache[T]:
    """Caché en memoria con caducidad por entrada y límite de tamaño (LRU).

    Cuando se llena, se descarta la entrada usada hace más tiempo. Cada entrada puede
    tener su propia duración, lo que permite cachear menos tiempo los 404 que los 200.
    """

    def __init__(self, max_entries: int, clock: Clock = time.monotonic) -> None:
        self._max_entries = max_entries
        self._clock = clock
        self._data: OrderedDict[str, tuple[float, T]] = OrderedDict()
        self.hits = 0
        self.misses = 0

    def get(self, key: str) -> T | None:
        item = self._data.get(key)
        if item is None:
            self.misses += 1
            return None
        expires_at, value = item
        if expires_at <= self._clock():
            del self._data[key]
            self.misses += 1
            return None
        self._data.move_to_end(key)
        self.hits += 1
        return value

    def set(self, key: str, value: T, ttl_seconds: float) -> None:
        if ttl_seconds <= 0:
            return
        self._data[key] = (self._clock() + ttl_seconds, value)
        self._data.move_to_end(key)
        while len(self._data) > self._max_entries:
            self._data.popitem(last=False)

    def clear(self) -> None:
        self._data.clear()

    def __len__(self) -> int:
        return len(self._data)


CircuitState = Literal["closed", "open", "half_open"]


class CircuitBreaker:
    """Corta las llamadas a un servicio que está fallando y lo vuelve a probar más tarde.

    - **closed**: funcionamiento normal. Se cuentan los fallos seguidos.
    - **open**: tras ``failure_threshold`` fallos seguidos se deja de llamar durante
      ``cooldown_seconds``. Las peticiones fallan al instante, sin esperar *timeouts*,
      y el servicio externo tiene tiempo de recuperarse.
    - **half_open**: pasado el tiempo de espera se deja pasar una llamada de prueba. Si
      sale bien se cierra el circuito; si falla, se vuelve a abrir.
    """

    def __init__(
        self,
        failure_threshold: int,
        cooldown_seconds: float,
        clock: Clock = time.monotonic,
    ) -> None:
        self._threshold = failure_threshold
        self._cooldown = cooldown_seconds
        self._clock = clock
        self._failures = 0
        self._opened_at: float | None = None

    @property
    def state(self) -> CircuitState:
        if self._opened_at is None:
            return "closed"
        if self._clock() - self._opened_at >= self._cooldown:
            return "half_open"
        return "open"

    def allow_request(self) -> bool:
        return self.state != "open"

    def record_success(self) -> None:
        self._failures = 0
        self._opened_at = None

    def record_failure(self) -> None:
        if self.state == "half_open":
            self._opened_at = self._clock()
            return
        self._failures += 1
        if self._failures >= self._threshold:
            self._opened_at = self._clock()
