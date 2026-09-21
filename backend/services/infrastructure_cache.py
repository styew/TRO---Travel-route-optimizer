"""Cache boundary; replace this process-local cache with a database later."""

from typing import Any


class InfrastructureCache:
    def __init__(self) -> None:
        self._entries: dict[tuple[float, float], dict[str, Any]] = {}

    @staticmethod
    def _key(latitude: float, longitude: float) -> tuple[float, float]:
        return round(latitude, 4), round(longitude, 4)

    def get(self, latitude: float, longitude: float) -> dict[str, Any] | None:
        return self._entries.get(self._key(latitude, longitude))

    def save(self, latitude: float, longitude: float, infrastructure: dict[str, Any]) -> None:
        self._entries[self._key(latitude, longitude)] = infrastructure
