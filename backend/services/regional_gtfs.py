"""Map selected railway terminals to the parent-station identifiers in GTFS."""

from __future__ import annotations

import csv
import os
import re
import unicodedata
import zipfile
from functools import lru_cache

REGIONAL_GTFS_ARCHIVE_PATH = os.getenv(
    # This must be the exact archive used to build otp-data/d-ticket/graph.obj.
    # Resolving a station from an older root-level export can produce an ID that
    # looks valid but does not exist in the running D-Ticket graph.
    "REGIONAL_GTFS_ARCHIVE_PATH", "/app/otp-data/d-ticket/regional-d-ticket-gtfs.zip"
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


@lru_cache(maxsize=8)
def _stops(archive_path: str) -> tuple[dict, ...]:
    """Load a GTFS stop index once for a small number of configured graphs."""
    try:
        with zipfile.ZipFile(archive_path) as archive:
            with archive.open("stops.txt") as stops_file:
                rows = csv.DictReader(
                    (line.decode("utf-8-sig") for line in stops_file)
                )
                return tuple(rows)
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
    for station in _stops(archive_path or REGIONAL_GTFS_ARCHIVE_PATH):
        # German regional and long-distance feeds expose parent stations.  Keep
        # that preferred behaviour for the existing local planners.
        if station.get("location_type") != "1" and station.get("parent_station"):
            continue
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


def find_gtfs_station_candidates(
    name: str, latitude: float, longitude: float, archive_path: str, limit: int = 3
) -> list[str]:
    """Return usable GTFS stop IDs for feeds that omit parent stations.

    Some international GTFS feeds only keep platform stops after filtering
    unused routes.  OTP can route from those stops, whereas the parent station
    may no longer exist.  This helper ranks both parent and platform IDs so the
    caller can validate the final ID against the live graph.
    """
    requested_words = _words(name)
    if not requested_words:
        return []
    matches: list[tuple[float, int, str]] = []
    for station in _stops(archive_path):
        station_words = _words(station.get("stop_name", ""))
        shared_words = requested_words & station_words
        if not shared_words:
            continue
        requires_main_station = "hauptbahnhof" in requested_words
        if requires_main_station and "hauptbahnhof" not in station_words:
            continue
        try:
            latitude_delta = float(station["stop_lat"]) - latitude
            longitude_delta = float(station["stop_lon"]) - longitude
        except (KeyError, TypeError, ValueError):
            continue
        # Exact word coverage outranks geographic proximity.  Parent stations
        # remain preferred when both parent and platform are present.
        missing_words = len(requested_words - station_words)
        platform_penalty = 1 if station.get("parent_station") else 0
        matches.append((missing_words * 10_000 + latitude_delta**2 + longitude_delta**2, platform_penalty, station["stop_id"]))
    return [station_id for _, _, station_id in sorted(matches)[:limit]]
