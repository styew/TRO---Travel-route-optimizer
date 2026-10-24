"""Build and query France's GTFS-backed city rail-hub index.

French cities commonly have several independent long-distance terminals.  This
module intentionally reads SNCF's GTFS parent stations instead of relying on
OpenStreetMap results, which may be incomplete or temporarily unavailable.
"""

from __future__ import annotations

import csv
import zipfile
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.database.models import (
    RailNetworkImportRecord,
    RailStationMembershipRecord,
    RailStationRecord,
)
from backend.database.session import SessionLocal
from backend.rail.countries.france import FRANCE_NETWORKS, FRANCE_RAIL_NETWORK
from backend.rail.station_registry import (
    _archive_fingerprint,
    _distance_km,
    _parent_stations,
    normalize_station_name,
)

CITY_HUB_RADIUS_KM = 35
MAX_CITY_RAIL_HUBS = 4


@dataclass(frozen=True)
class FranceRegistryBuildSummary:
    """Describe one auditable import of the current French GTFS archive."""

    stations: int
    memberships: int
    network_key: str


def _canonical_id(row: dict[str, str]) -> str:
    """Return a stable French station identity based on GTFS name and location."""
    return (
        f"FR:{normalize_station_name(row['stop_name'])}:"
        f"{float(row['stop_lat']):.3f}:{float(row['stop_lon']):.3f}"
    )


def _parent_service_scores(archive_path: str) -> dict[str, int]:
    """Count distinct rail-service stop groups beneath each GTFS parent station.

    SNCF exposes operator-specific child stops under one physical parent.  The
    count is a small local ranking signal: a TGV/TER hub outranks a nearby bus
    terminal without maintaining a hand-written list of French cities.
    """
    groups: dict[str, set[str]] = defaultdict(set)
    with zipfile.ZipFile(archive_path) as archive:
        with archive.open("stops.txt") as stops_file:
            for row in csv.DictReader(line.decode("utf-8-sig") for line in stops_file):
                parent = row.get("parent_station") or ""
                stop_id = row.get("stop_id") or ""
                if not parent or not stop_id or "car" in stop_id.casefold():
                    continue
                groups[parent].add(stop_id.split("-", maxsplit=1)[0])
    return {parent: max(1, len(service_groups)) for parent, service_groups in groups.items()}


def build_france_station_registry(session: Session) -> FranceRegistryBuildSummary:
    """Import SNCF parent stations into the persistent country-hub registry.

    The import is idempotent and never modifies the GTFS archive.  Parent
    stations are used deliberately: platform and operator-specific child stops
    would otherwise create duplicate Paris and Lyon terminals in the UI.
    """
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    stations = {
        record.canonical_id: record
        for record in session.scalars(select(RailStationRecord)).all()
    }
    memberships = {
        (record.network_key, record.source_stop_id): record
        for record in session.scalars(select(RailStationMembershipRecord)).all()
    }
    total_memberships = 0

    for network in FRANCE_NETWORKS:
        archive_path = Path(network.archive_path)
        if not archive_path.is_file():
            raise FileNotFoundError(f"The {network.key} GTFS archive was not found at {archive_path}.")
        parents = _parent_stations(str(archive_path))
        service_scores = _parent_service_scores(str(archive_path))
        for parent in parents:
            if not parent.get("stop_name") or not parent.get("stop_lat") or not parent.get("stop_lon"):
                continue
            canonical_id = _canonical_id(parent)
            station = stations.get(canonical_id)
            if station is None:
                station = RailStationRecord(
                    canonical_id=canonical_id,
                    home_region="FR",
                    name=parent["stop_name"],
                    normalized_name=normalize_station_name(parent["stop_name"]),
                    latitude=float(parent["stop_lat"]),
                    longitude=float(parent["stop_lon"]),
                    created_at=now,
                    updated_at=now,
                )
                session.add(station)
                session.flush()
                stations[canonical_id] = station
            else:
                station.updated_at = now

            key = (network.key, parent["stop_id"])
            membership = memberships.get(key)
            if membership is None:
                membership = RailStationMembershipRecord(
                    station_id=station.id,
                    network_key=network.key,
                    source_stop_id=parent["stop_id"],
                    source_name=parent["stop_name"],
                    latitude=float(parent["stop_lat"]),
                    longitude=float(parent["stop_lon"]),
                    is_parent_station=True,
                    service_score=service_scores.get(parent["stop_id"], 1),
                    imported_at=now,
                )
                session.add(membership)
                memberships[key] = membership
            else:
                membership.station_id = station.id
                membership.source_name = parent["stop_name"]
                membership.latitude = float(parent["stop_lat"])
                membership.longitude = float(parent["stop_lon"])
                membership.service_score = service_scores.get(parent["stop_id"], 1)
                membership.imported_at = now
            total_memberships += 1

        import_record = session.scalar(
            select(RailNetworkImportRecord).where(RailNetworkImportRecord.network_key == network.key)
        )
        values = {
            "archive_path": str(archive_path),
            "archive_fingerprint": _archive_fingerprint(archive_path),
            "parent_station_count": len(parents),
            "imported_at": now,
        }
        if import_record is None:
            session.add(RailNetworkImportRecord(network_key=network.key, **values))
        else:
            for field, value in values.items():
                setattr(import_record, field, value)

    session.commit()
    return FranceRegistryBuildSummary(
        stations=len(stations),
        memberships=total_memberships,
        network_key=FRANCE_RAIL_NETWORK.key,
    )


def _matches_city(station_name: str, city_name: str) -> bool:
    """Require every normalized city word in a GTFS parent-station name."""
    city_words = set(normalize_station_name(city_name).split())
    station_words = set(normalize_station_name(station_name).split())
    return bool(city_words) and city_words.issubset(station_words)


def _rank_city_hubs(records: list[tuple[RailStationRecord, RailStationMembershipRecord]], city: dict) -> list[dict]:
    """Select nearby, city-named SNCF parent stations for a single city search."""
    hubs: list[dict] = []
    seen: set[str] = set()
    for station, membership in records:
        if station.canonical_id in seen or not _matches_city(station.name, city["city_name"]):
            continue
        distance_km = _distance_km(
            city["latitude"], city["longitude"], station.latitude, station.longitude
        )
        if distance_km > CITY_HUB_RADIUS_KM:
            continue
        seen.add(station.canonical_id)
        hubs.append({
            "id": f"gtfs:{FRANCE_RAIL_NETWORK.key}:{station.canonical_id}",
            "name": station.name,
            "latitude": station.latitude,
            "longitude": station.longitude,
            "type": "train_station",
            "distance_km": round(distance_km, 2),
            "iata": None,
            "icao": None,
            "source": "sncf-gtfs",
            "gtfs_stop_ids": {FRANCE_RAIL_NETWORK.key: membership.source_stop_id},
            "rail_service_score": membership.service_score,
        })
    return sorted(
        hubs, key=lambda hub: (-hub["rail_service_score"], hub["distance_km"], hub["name"])
    )[:MAX_CITY_RAIL_HUBS]


def find_french_city_hubs(city: dict) -> list[dict]:
    """Return cached French GTFS hubs without making a network request.

    An empty result means that the scheduled GTFS registry import has not yet
    run, or that no city-named SNCF parent station exists near the geocoded
    centre.  Callers must not fall back to Overpass for French rail discovery.
    """
    with SessionLocal() as session:
        records = list(session.execute(
            select(RailStationRecord, RailStationMembershipRecord)
            .join(RailStationMembershipRecord, RailStationMembershipRecord.station_id == RailStationRecord.id)
            .where(RailStationMembershipRecord.network_key == FRANCE_RAIL_NETWORK.key)
        ).all())
    return _rank_city_hubs(records, city)
