"""Build and query a canonical station registry from modular GTFS archives."""

from __future__ import annotations

import csv
import hashlib
import re
import unicodedata
import zipfile
from dataclasses import dataclass
from datetime import datetime, timezone
from math import asin, cos, radians, sin, sqrt
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.database.models import (
    RailNetworkImportRecord,
    RailStationInterchangeRecord,
    RailStationMembershipRecord,
    RailStationRecord,
)
from backend.rail.countries.germany import (
    DEFAULT_SAME_STATION_TRANSFER_MINUTES,
    GERMANY_LONG_DISTANCE_NETWORK,
    GERMANY_NETWORKS,
    GERMANY_REGIONAL_NETWORK,
    RailNetworkManifest,
)


@dataclass(frozen=True)
class RegistryBuildSummary:
    """Return the counts produced by one repeatable registry build."""

    stations: int
    memberships: int
    interchanges: int
    networks: tuple[str, ...]


@dataclass(frozen=True)
class InterchangeCandidate:
    """Describe a same-station handover selected for a route search."""

    canonical_station_id: str
    name: str
    latitude: float
    longitude: float
    from_stop_id: str
    to_stop_id: str
    minimum_transfer_minutes: int
    distance_km: float


def normalize_station_name(value: str) -> str:
    """Create a conservative comparable station name across GTFS providers."""
    value = unicodedata.normalize("NFKD", value)
    value = "".join(character for character in value if not unicodedata.combining(character))
    value = value.casefold().replace("hbf", "hauptbahnhof")
    value = re.sub(r"[^a-z0-9]+", " ", value)
    return " ".join(value.split())


def _archive_fingerprint(path: Path) -> str:
    """Fingerprint an archive so database viewers can identify the loaded source."""
    digest = hashlib.sha256()
    with path.open("rb") as archive:
        for block in iter(lambda: archive.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _parent_stations(archive_path: str) -> list[dict[str, str]]:
    """Read parent stations; platform rows are intentionally not transfer anchors."""
    with zipfile.ZipFile(archive_path) as archive:
        with archive.open("stops.txt") as stops_file:
            stops = csv.DictReader(line.decode("utf-8-sig") for line in stops_file)
            return [row for row in stops if row.get("location_type") == "1"]


def _canonical_id(region: str, row: dict[str, str]) -> str:
    """Create a deterministic physical-station key from name and precise location.

    GTFS.DE's two German feeds do not share parent station identifiers, but they
    publish compatible names and coordinates.  Keeping the coordinate in the
    key prevents two identically named towns from being silently merged.
    """
    name = normalize_station_name(row.get("stop_name", ""))
    latitude = round(float(row["stop_lat"]), 3)
    longitude = round(float(row["stop_lon"]), 3)
    return f"{region}:{name}:{latitude:.3f}:{longitude:.3f}"


def _distance_km(latitude_a: float, longitude_a: float, latitude_b: float, longitude_b: float) -> float:
    """Calculate a geographic distance used only to rank a bounded candidate list."""
    latitude_delta = radians(latitude_b - latitude_a)
    longitude_delta = radians(longitude_b - longitude_a)
    haversine = sin(latitude_delta / 2) ** 2 + cos(radians(latitude_a)) * cos(radians(latitude_b)) * sin(longitude_delta / 2) ** 2
    return 6371.0088 * 2 * asin(sqrt(haversine))


def build_germany_station_registry(session: Session) -> RegistryBuildSummary:
    """Import German GTFS parent stations and create explicit regional↔ICE joins.

    The operation is idempotent: running it again updates source metadata and
    preserves the canonical station identities used by future route results.
    It does not call an external API and it does not build an OTP graph.
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

    for network in GERMANY_NETWORKS:
        archive_path = Path(network.archive_path)
        if not archive_path.is_file():
            raise FileNotFoundError(
                f"The {network.key} GTFS archive was not found at {archive_path}."
            )
        parents = _parent_stations(str(archive_path))
        for parent in parents:
            if not parent.get("stop_name") or not parent.get("stop_lat") or not parent.get("stop_lon"):
                continue
            canonical_id = _canonical_id("DE", parent)
            station = stations.get(canonical_id)
            if station is None:
                station = RailStationRecord(
                    canonical_id=canonical_id,
                    home_region="DE",
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
                    imported_at=now,
                )
                session.add(membership)
                memberships[key] = membership
            else:
                membership.station_id = station.id
                membership.source_name = parent["stop_name"]
                membership.latitude = float(parent["stop_lat"])
                membership.longitude = float(parent["stop_lon"])
                membership.imported_at = now
            total_memberships += 1

        import_record = session.scalar(
            select(RailNetworkImportRecord).where(RailNetworkImportRecord.network_key == network.key)
        )
        values = dict(
            archive_path=str(archive_path),
            archive_fingerprint=_archive_fingerprint(archive_path),
            parent_station_count=len(parents),
            imported_at=now,
        )
        if import_record is None:
            session.add(RailNetworkImportRecord(network_key=network.key, **values))
        else:
            for field, value in values.items():
                setattr(import_record, field, value)

    session.flush()
    regional_memberships = {
        membership.station_id: membership
        for membership in session.scalars(
            select(RailStationMembershipRecord).where(
                RailStationMembershipRecord.network_key == GERMANY_REGIONAL_NETWORK.key
            )
        ).all()
    }
    long_distance_memberships = {
        membership.station_id: membership
        for membership in session.scalars(
            select(RailStationMembershipRecord).where(
                RailStationMembershipRecord.network_key == GERMANY_LONG_DISTANCE_NETWORK.key
            )
        ).all()
    }
    existing_interchanges = {
        (item.station_id, item.from_network_key, item.to_network_key): item
        for item in session.scalars(select(RailStationInterchangeRecord)).all()
    }
    interchange_count = 0
    for station_id in regional_memberships.keys() & long_distance_memberships.keys():
        for from_network, to_network in (
            (GERMANY_REGIONAL_NETWORK.key, GERMANY_LONG_DISTANCE_NETWORK.key),
            (GERMANY_LONG_DISTANCE_NETWORK.key, GERMANY_REGIONAL_NETWORK.key),
        ):
            key = (station_id, from_network, to_network)
            interchange = existing_interchanges.get(key)
            if interchange is None:
                session.add(RailStationInterchangeRecord(
                    station_id=station_id,
                    from_network_key=from_network,
                    to_network_key=to_network,
                    minimum_transfer_minutes=DEFAULT_SAME_STATION_TRANSFER_MINUTES,
                    transfer_kind="same_station",
                    imported_at=now,
                ))
            else:
                interchange.imported_at = now
            interchange_count += 1

    session.commit()
    return RegistryBuildSummary(
        stations=len(stations), memberships=total_memberships,
        interchanges=interchange_count, networks=tuple(network.key for network in GERMANY_NETWORKS),
    )


def nearest_interchanges(
    session: Session, latitude: float, longitude: float, limit: int = 10
) -> list[InterchangeCandidate]:
    """Return up to ten canonical regional↔long-distance handover stations.

    This is the bounded first stage of the new railway search.  Timetable
    routing later decides which of these candidates are actually reachable;
    geography alone never creates a connection in the route graph.
    """
    interchanges = session.scalars(
        select(RailStationInterchangeRecord).where(
            RailStationInterchangeRecord.from_network_key == GERMANY_REGIONAL_NETWORK.key,
            RailStationInterchangeRecord.to_network_key == GERMANY_LONG_DISTANCE_NETWORK.key,
        )
    ).all()
    stations = {station.id: station for station in session.scalars(select(RailStationRecord)).all()}
    memberships = session.scalars(select(RailStationMembershipRecord)).all()
    by_station_network = {(item.station_id, item.network_key): item for item in memberships}
    candidates: list[InterchangeCandidate] = []
    for interchange in interchanges:
        station = stations.get(interchange.station_id)
        regional = by_station_network.get((interchange.station_id, interchange.from_network_key))
        long_distance = by_station_network.get((interchange.station_id, interchange.to_network_key))
        if station is None or regional is None or long_distance is None:
            continue
        candidates.append(InterchangeCandidate(
            canonical_station_id=station.canonical_id,
            name=station.name,
            latitude=station.latitude,
            longitude=station.longitude,
            from_stop_id=regional.source_stop_id,
            to_stop_id=long_distance.source_stop_id,
            minimum_transfer_minutes=interchange.minimum_transfer_minutes,
            distance_km=round(_distance_km(latitude, longitude, station.latitude, station.longitude), 2),
        ))
    return sorted(candidates, key=lambda item: item.distance_km)[:limit]
