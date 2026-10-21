"""Map selected railway terminals to the parent-station identifiers in GTFS."""

from __future__ import annotations

import csv
import os
import re
import unicodedata
import zipfile
from functools import lru_cache

REGIONAL_GTFS_ARCHIVE_PATH = os.getenv(
    "REGIONAL_GTFS_ARCHIVE_PATH", "/app/otp-data/regional-rail-gtfs.zip"
)
_IGNORED_CITY_WORDS = {"am", "an", "der", "im", "in", "s", "u"}


def _words(value: str) -> set[str]:
    """Create comparable name tokens across OSM, Nominatim, and GTFS naming styles."""
    normalized = unicodedata.normalize("NFKD", value)
    normalized = "".join(char for char in normalized if not unicodedata.combining(char))
    words = {
        "hauptbahnhof" if word == "hbf" else word
        for word in re.sub(r"[^a-z0-9]+", " ", normalized.lower()).split()
        if word not in _IGNORED_CITY_WORDS
    }
    return words


@lru_cache(maxsize=1)
def _parent_stations(archive_path: str) -> tuple[dict, ...]:
    """Load only parent stations once; platform-level stops are not valid journey anchors."""
    try:
        with zipfile.ZipFile(archive_path) as archive:
            with archive.open("stops.txt") as stops_file:
                rows = csv.DictReader(
                    (line.decode("utf-8-sig") for line in stops_file)
                )
                return tuple(
                    row
                    for row in rows
                    if row.get("location_type") == "1" or not row.get("parent_station")
                )
    except (FileNotFoundError, KeyError, zipfile.BadZipFile) as error:
        raise RuntimeError("The regional GTFS archive is not available.") from error


def find_gtfs_station_id(
    name: str, latitude: float, longitude: float, archive_path: str | None = None
) -> str | None:
    """Return the best GTFS parent station from the requested feed archive.

    The optional archive keeps the regional and long-distance datasets separate:
    a valid station identifier from one OpenTripPlanner graph cannot be used in
    the other graph.
    """
    requested_words = _words(name)
    if not requested_words:
        return None
    matches: list[tuple[float, str]] = []
    for station in _parent_stations(archive_path or REGIONAL_GTFS_ARCHIVE_PATH):
        station_words = _words(station.get("stop_name", ""))
        shared_words = requested_words & station_words
        requires_main_station = "hauptbahnhof" in requested_words
        has_enough_shared_words = len(shared_words) >= min(2, len(requested_words))
        if not has_enough_shared_words:
            continue
        if requires_main_station and "hauptbahnhof" not in station_words:
            continue
        try:
            latitude_delta = float(station["stop_lat"]) - latitude
            longitude_delta = float(station["stop_lon"]) - longitude
        except (KeyError, TypeError, ValueError):
            continue
        matches.append((latitude_delta**2 + longitude_delta**2, station["stop_id"]))
    return min(matches)[1] if matches else None
