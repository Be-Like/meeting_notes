"""The mesh's result cache. Imports nothing local, so another backend is a new class, not a rewrite."""

import json
import threading
import time
from typing import Any, Callable, Protocol


class Cache(Protocol):
    def get(self, key: str) -> Any | None: ...  # None is a miss
    def set(self, key: str, value: Any) -> None: ...
    def clear(self, service: str) -> None: ...  # every entry for the service, every user


def cache_key(service: str, method: str, user_id: int, arguments: dict[str, Any]) -> str:
    """Service and method first so clear is a prefix match; arguments sorted so keyword order never matters."""
    return f"{service}.{method}|{user_id}|{json.dumps(arguments, sort_keys=True, default=str)}"


class InMemoryCache:
    """A dict of (expires_at, value) behind one lock. Expired entries are evicted on read; there is no sweeper."""

    def __init__(self, ttl_seconds: float = 60, clock: Callable[[], float] = time.monotonic) -> None:
        self.ttl_seconds = ttl_seconds
        self._clock = clock
        self._entries: dict[str, tuple[float, Any]] = {}
        self._lock = threading.Lock()

    def get(self, key: str) -> Any | None:
        with self._lock:
            entry = self._entries.get(key)
            if entry is None:
                return None
            expires_at, value = entry
            if expires_at <= self._clock():
                del self._entries[key]
                return None
            return value

    def set(self, key: str, value: Any) -> None:
        with self._lock:
            self._entries[key] = (self._clock() + self.ttl_seconds, value)

    def clear(self, service: str) -> None:
        prefix = f"{service}."
        with self._lock:
            for key in [key for key in self._entries if key.startswith(prefix)]:
                del self._entries[key]
