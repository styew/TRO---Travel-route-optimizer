"""Declare corridor inputs without loading unrelated country data."""

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class CorridorManifest:
    """Identify a bidirectional country pair and its independently built index."""

    key: str
    countries: frozenset[str]
    index_path: str
    timezone: str = "Europe/Paris"
    max_stations_per_country: int = 8


GERMANY_FRANCE = CorridorManifest(
    key="de-fr", countries=frozenset({"DE", "FR"}),
    index_path=os.getenv("DE_FR_TIMETABLE_PATH", "data/rail/de-fr.sqlite"),
)


def select_corridor(origin: str | None, destination: str | None) -> CorridorManifest | None:
    """Select a pair in either direction, independently of ticket ownership."""
    if origin != destination and frozenset({origin, destination}) == GERMANY_FRANCE.countries:
        return GERMANY_FRANCE
    return None
