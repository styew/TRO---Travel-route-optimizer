"""Describe the small rail timetable graphs that TRO can use on demand.

The catalog contains metadata only.  It deliberately does not load GTFS files
or start OpenTripPlanner services.  A search first selects a corridor from
country codes, then asks only that corridor's running planner for schedules.
"""

import csv
import io
import re
import zipfile
from dataclasses import dataclass
from functools import lru_cache
from math import cos, radians
from pathlib import Path

from backend.models.node import TransportNode


_LONG_DISTANCE_PLATFORM = re.compile(
    r"(?:OCE(?:ICE|TGV|INTERCITES|LYRIA)|(?:^|[-_:])(?:ICE|TGV|IC|EC|RJ|NJ)(?:[-_:]|$))",
    re.IGNORECASE,
)


def _is_long_distance_platform(stop_id: str) -> bool:
    """Recognize service codes without mistaking the letters in ``OCECar`` for EC."""
    return bool(_LONG_DISTANCE_PLATFORM.search(stop_id))


@dataclass(frozen=True)
class InternationalRailGateway:
    """A schedule-derived German station where D-Ticket travel can hand over."""

    key: str
    name: str
    latitude: float
    longitude: float
    destination_country: str
    minimum_transfer_minutes: int = 15

    def as_node(self) -> TransportNode:
        """Create the graph node without implying car access or a ticket offer."""
        return TransportNode(
            id=f"tro:international-rail-gateway:{self.key}",
            type="train_station",
            name=self.name,
            latitude=self.latitude,
            longitude=self.longitude,
            supported_modes=["train"],
            source="tro-rail-network-catalog",
            source_id=self.key,
            metadata={
                "graph_role": "origin_international_rail_gateway",
                "country_code": "DE",
                "destination_country_code": self.destination_country,
                "minimum_international_transfer_minutes": self.minimum_transfer_minutes,
                "ticket_boundary": "Deutschlandticket_to_paid_international_rail",
            },
        )


@dataclass(frozen=True)
class RailCorridor:
    """Identify one bounded timetable graph and its supported ticket handover."""

    key: str
    origin_country: str
    destination_country: str
    graph_url_environment: str
    gtfs_archives: tuple[str, ...]
    # The candidate stations come from the GTFS files, never a city list kept
    # in code. This cap keeps each request bounded as Europe grows.
    max_gateway_candidates: int = 12


EUROPE_RAIL_CORRIDORS = (
    RailCorridor(
        key="de-fr-rail",
        origin_country="DE",
        destination_country="FR",
        graph_url_environment="OTP_EUROPE_URL",
        gtfs_archives=(
            "/app/otp-data/europe/rail/france-sncf-gtfs.zip",
            "/app/otp-data/europe/rail/switzerland-national-gtfs.zip",
        ),
    ),
)


def select_rail_corridor(origin_country: str | None, destination_country: str | None) -> RailCorridor | None:
    """Return the one configured corridor for this country pair, if available."""
    for corridor in EUROPE_RAIL_CORRIDORS:
        if corridor.origin_country == origin_country and corridor.destination_country == destination_country:
            return corridor
    return None


def _station_code(stop_id: str) -> str | None:
    """Return the suffix shared by GTFS parent-station and platform records."""
    match = re.search(r"(\d{8})$", stop_id)
    return match.group(1) if match else None


def _archive_path(path: str) -> Path:
    """Use Docker's mounted path, with a project-relative fallback for local runs."""
    mounted_path = Path(path)
    if mounted_path.exists():
        return mounted_path
    return Path(__file__).resolve().parents[2] / "otp-data" / "europe" / "rail" / mounted_path.name


@lru_cache(maxsize=16)
def _discover_archive_gateway_stations(archive_path: str) -> tuple[InternationalRailGateway, ...]:
    """Read German international interchange stations from a filtered GTFS archive.

    A station qualifies only if its German GTFS parent station shares an ID
    with an ICE/TGV/IC/EC/RJ/NJ platform in that same timetable feed. This is
    schedule discovery, not a manually maintained Mannheim/Karlsruhe list.
    """
    try:
        with zipfile.ZipFile(archive_path) as archive, archive.open("stops.txt") as file:
            rows = list(csv.DictReader(io.TextIOWrapper(file, encoding="utf-8-sig")))
    except (OSError, KeyError, zipfile.BadZipFile, UnicodeError):
        return ()

    german_areas: dict[str, dict[str, str]] = {}
    international_platform_codes: set[str] = set()
    for row in rows:
        stop_id = row.get("stop_id", "")
        code = _station_code(stop_id)
        if code is None:
            continue
        # SNCF's imported German stations use OCE80… identifiers. This avoids
        # accidentally treating nearby French, Swiss, or Luxembourg stops as
        # a Germany-side D-Ticket handover.
        if stop_id.startswith("StopArea:OCE80"):
            german_areas[code] = row
        if stop_id.startswith("StopPoint:") and _is_long_distance_platform(stop_id):
            international_platform_codes.add(code)

    candidates: list[InternationalRailGateway] = []
    for code in sorted(german_areas.keys() & international_platform_codes):
        row = german_areas[code]
        try:
            latitude, longitude = float(row["stop_lat"]), float(row["stop_lon"])
        except (KeyError, TypeError, ValueError):
            continue
        name = (row.get("stop_name") or "").strip()
        if name:
            candidates.append(InternationalRailGateway(
                key=f"gtfs-{code}", name=name, latitude=latitude, longitude=longitude,
                destination_country="",
            ))
    return tuple(candidates)


def _distance_score(origin: TransportNode, station: InternationalRailGateway) -> float:
    """Rank stations near the user's origin without costly routing requests."""
    longitude_scale = cos(radians((origin.latitude + station.latitude) / 2))
    return (origin.latitude - station.latitude) ** 2 + (
        (origin.longitude - station.longitude) * longitude_scale
    ) ** 2


def corridor_gateway_nodes(
    corridor: RailCorridor | None, origin: TransportNode | None = None,
) -> list[TransportNode]:
    """Discover and bound international handover stations for this specific search.

    The archive scan is cached once per backend process. Each request then
    considers only the closest timetable-backed candidates, instead of loading
    or searching all of Europe.
    """
    if corridor is None:
        return []
    candidates: dict[str, InternationalRailGateway] = {}
    for archive in corridor.gtfs_archives:
        for station in _discover_archive_gateway_stations(str(_archive_path(archive))):
            candidates.setdefault(station.key, InternationalRailGateway(
                key=station.key, name=station.name, latitude=station.latitude,
                longitude=station.longitude, destination_country=corridor.destination_country,
            ))
    stations = list(candidates.values())
    if origin is not None:
        stations.sort(key=lambda station: _distance_score(origin, station))
    return [station.as_node() for station in stations[:corridor.max_gateway_candidates]]


@lru_cache(maxsize=32)
def _discover_city_long_distance_stations(
    archive_path: str, city_key: str,
) -> tuple[tuple[str, str], ...]:
    """Find timetable terminals for a destination city from the GTFS archive.

    A geocoded city centre is not itself a railway platform. In large cities
    such as Paris, resolving only ``Paris`` geographically can select a
    regional terminal even when the timetable connects to Paris Est. This
    cache returns actual long-distance terminal names from the corridor feed.
    """
    city_tokens = set(re.findall(r"[a-z0-9]+", city_key.casefold()))
    if not city_tokens:
        return ()
    try:
        with zipfile.ZipFile(archive_path) as archive, archive.open("stops.txt") as file:
            rows = csv.DictReader(io.TextIOWrapper(file, encoding="utf-8-sig"))
            stations: list[tuple[str, str]] = []
            seen: set[str] = set()
            for row in rows:
                stop_id = row.get("stop_id", "")
                name = (row.get("stop_name") or "").strip()
                if not _is_long_distance_platform(stop_id):
                    continue
                name_tokens = set(re.findall(r"[a-z0-9]+", name.casefold()))
                if not city_tokens.issubset(name_tokens) or name.casefold() in seen:
                    continue
                # OTP exposes the first loaded GTFS feed as namespace ``0``.
                # Retaining this exact platform ID avoids a later text search
                # selecting a same-named TER platform at the terminal.
                stations.append((name, f"0:{stop_id}"))
                seen.add(name.casefold())
    except (OSError, KeyError, zipfile.BadZipFile, UnicodeError):
        return ()
    return tuple(stations)


@lru_cache(maxsize=32)
def _discover_city_long_distance_station_names(
    archive_path: str, city_key: str,
) -> tuple[str, ...]:
    """Return terminal labels for diagnostics and compatibility callers."""
    return tuple(name for name, _ in _discover_city_long_distance_stations(
        archive_path, city_key,
    ))


def corridor_destination_station_names(
    corridor: RailCorridor | None, destination: TransportNode,
    limit: int = 8,
) -> list[str]:
    """Return real long-distance terminal names for one corridor destination."""
    if corridor is None:
        return []
    names: list[str] = []
    for archive in corridor.gtfs_archives:
        names.extend(_discover_city_long_distance_station_names(
            str(_archive_path(archive)), destination.name,
        ))
    return list(dict.fromkeys(names))[:limit]


def corridor_destination_station_locations(
    corridor: RailCorridor | None, destination: TransportNode,
    limit: int = 8,
) -> list[dict[str, str]]:
    """Return GTFS-backed terminal labels with exact live OTP platform IDs."""
    if corridor is None:
        return []
    terminals: list[dict[str, str]] = []
    names_seen: set[str] = set()
    for archive in corridor.gtfs_archives:
        for name, stop_location_id in _discover_city_long_distance_stations(
            str(_archive_path(archive)), destination.name,
        ):
            if name.casefold() in names_seen:
                continue
            terminals.append({"name": name, "stop_location_id": stop_location_id})
            names_seen.add(name.casefold())
            if len(terminals) == limit:
                return terminals
    return terminals
