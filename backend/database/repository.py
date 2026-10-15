"""Persist and retrieve approved initial transport nodes for a city."""

from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, select

from backend.database.models import (
    CityNodeCandidateRecord,
    CityRecord,
    TransportNodeRecord,
)
from backend.database.session import SessionLocal

CACHE_TTL_DAYS = 30
INFRASTRUCTURE_FILTER_VERSION = 3


def _utc_now() -> datetime:
    """Return a timezone-naive UTC timestamp compatible with SQLite storage."""
    return datetime.now(UTC).replace(tzinfo=None)


class CityNodeRepository:
    """Provides persistent cache operations for a city's selected infrastructure."""

    def get_infrastructure(self, city_source_id: str) -> dict | None:
        """Return unexpired selected stations and airports for a previously searched city."""
        now = _utc_now()
        with SessionLocal() as session:
            city = session.scalar(
                select(CityRecord).where(
                    CityRecord.source_id == city_source_id,
                    CityRecord.expires_at > now,
                )
            )
            if city is None:
                return None
            if city.infrastructure_filter_version != INFRASTRUCTURE_FILTER_VERSION:
                return None

            rows = session.execute(
                select(TransportNodeRecord, CityNodeCandidateRecord)
                .join(
                    CityNodeCandidateRecord,
                    CityNodeCandidateRecord.transport_node_id == TransportNodeRecord.id,
                )
                .where(
                    CityNodeCandidateRecord.city_id == city.id,
                    CityNodeCandidateRecord.selected.is_(True),
                )
            ).all()

        infrastructure = {"train_stations": [], "airports": []}
        for node, candidate in rows:
            normalized_node = {
                "id": node.source_id,
                "name": node.name,
                "latitude": node.latitude,
                "longitude": node.longitude,
                "type": node.node_type,
                "distance_km": candidate.distance_km,
                "iata": node.iata,
                "icao": node.icao,
                "source": node.source,
            }
            if node.node_type == "train_station":
                infrastructure["train_stations"].append(normalized_node)
            elif node.node_type == "airport":
                infrastructure["airports"].append(normalized_node)
        return infrastructure

    def save_infrastructure(self, city: dict, infrastructure: dict) -> None:
        """Upsert a city and replace its selected infrastructure with the latest result."""
        now = _utc_now()
        expires_at = now + timedelta(days=CACHE_TTL_DAYS)

        with SessionLocal() as session:
            city_record = session.scalar(
                select(CityRecord).where(CityRecord.source_id == city["source_id"])
            )
            if city_record is None:
                city_record = CityRecord(source_id=city["source_id"])
                session.add(city_record)

            city_record.city_name = city["city_name"]
            city_record.display_name = city["name"]
            city_record.latitude = city["latitude"]
            city_record.longitude = city["longitude"]
            city_record.source = city["source"]
            city_record.infrastructure_filter_version = INFRASTRUCTURE_FILTER_VERSION
            city_record.fetched_at = now
            city_record.expires_at = expires_at
            session.flush()

            session.execute(
                delete(CityNodeCandidateRecord).where(
                    CityNodeCandidateRecord.city_id == city_record.id
                )
            )

            for node_type, candidates in infrastructure.items():
                selection_rule = (
                    "city_train_station_filter"
                    if node_type == "train_stations"
                    else "city_commercial_airport_filter"
                )
                for candidate in candidates:
                    node_record = session.scalar(
                        select(TransportNodeRecord).where(
                            TransportNodeRecord.source_id == candidate["id"]
                        )
                    )
                    if node_record is None:
                        node_record = TransportNodeRecord(source_id=candidate["id"])
                        session.add(node_record)

                    node_record.node_type = candidate["type"]
                    node_record.name = candidate["name"]
                    node_record.latitude = candidate["latitude"]
                    node_record.longitude = candidate["longitude"]
                    node_record.iata = candidate.get("iata")
                    node_record.icao = candidate.get("icao")
                    node_record.source = candidate["source"]
                    node_record.updated_at = now
                    session.flush()

                    session.add(
                        CityNodeCandidateRecord(
                            city_id=city_record.id,
                            transport_node_id=node_record.id,
                            distance_km=candidate["distance_km"],
                            selected=True,
                            selection_rule=selection_rule,
                            fetched_at=now,
                        )
                    )

            session.commit()
