"""Small, dependency-free request throttling primitives for provider adapters."""

from __future__ import annotations

from collections.abc import Callable
from threading import Lock
from time import monotonic, sleep


class RequestThrottle:
    """Serialize requests and enforce a minimum interval between them.

    The throttle is deliberately process-local. Provider adapters still need to
    respect the vendor's account-wide quota and retry policy; this class only
    prevents a single application process from issuing bursts of requests.
    """

    def __init__(
        self,
        min_interval: float,
        *,
        clock: Callable[[], float] = monotonic,
        sleeper: Callable[[float], None] = sleep,
    ) -> None:
        if min_interval < 0:
            raise ValueError("min_interval must be non-negative")
        self.min_interval = float(min_interval)
        self._clock = clock
        self._sleeper = sleeper
        self._lock = Lock()
        self._next_allowed = 0.0

    def wait(self) -> None:
        """Block until the next request is allowed."""
        if self.min_interval == 0:
            return
        with self._lock:
            now = self._clock()
            delay = self._next_allowed - now
            if delay > 0:
                self._sleeper(delay)
                now = self._clock()
            self._next_allowed = max(now, self._next_allowed) + self.min_interval
