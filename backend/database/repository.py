"""Persist and retrieve approved initial transport nodes for a city."""

from datetime import UTC, date, datetime, timedelta

from sqlalchemy import delete, select

from backend.database.models import (
    AirportScheduleFetchRecord,
    CityNodeCandidateRecord,
    CityRecord,
    FlightReferenceRecord,
    FlightPriceObservationRecord,
    GatewayAirportRecord,
    ScheduledFlightRecord,
    TransportNodeRecord,
)
from backend.models.graph import TravelEdge
from backend.database.session import SessionLocal

CACHE_TTL_DAYS = 30
INFRASTRUCTURE_FILTER_VERSION = 5
FLIGHT_REFERENCE_CACHE_TTL_HOURS = 24
GATEWAY_AIRPORT_CACHE_TTL_DAYS = 30
SCHEDULE_CACHE_TTL_DAYS = 7


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


class FlightReferenceRepository:
    """Caches recent flight references without presenting them as live prices."""

    def save_edges(
        self, origin_iata: str, destination_iata: str, departure_date: str,
        edges: list[TravelEdge],
    ) -> None:
        """Replace the cached response after a provider returns priced flight edges."""
        if not edges:
            return
        now = _utc_now()
        with SessionLocal() as session:
            record = session.scalar(
                select(FlightReferenceRecord).where(
                    FlightReferenceRecord.origin_iata == origin_iata,
                    FlightReferenceRecord.destination_iata == destination_iata,
                    FlightReferenceRecord.departure_date == departure_date,
                )
            )
            if record is None:
                record = FlightReferenceRecord(
                    origin_iata=origin_iata,
                    destination_iata=destination_iata,
                    departure_date=departure_date,
                    edge_payloads=[],
                    fetched_at=now,
                    expires_at=now,
                )
                session.add(record)
            record.edge_payloads = [edge.model_dump(mode="json") for edge in edges]
            record.fetched_at = now
            record.expires_at = now + timedelta(hours=FLIGHT_REFERENCE_CACHE_TTL_HOURS)
            FlightPriceObservationRepository.add_edges_in_session(
                session, origin_iata, destination_iata, departure_date, edges, now
            )
            session.commit()

    def get_edges(
        self, origin_iata: str, destination_iata: str, departure_date: str,
        origin_node_id: str, destination_node_id: str,
    ) -> list[TravelEdge]:
        """Return an unexpired stored reference remapped to the current graph nodes."""
        now = _utc_now()
        with SessionLocal() as session:
            record = session.scalar(
                select(FlightReferenceRecord).where(
                    FlightReferenceRecord.origin_iata == origin_iata,
                    FlightReferenceRecord.destination_iata == destination_iata,
                    FlightReferenceRecord.departure_date == departure_date,
                    FlightReferenceRecord.expires_at > now,
                )
            )
            if record is None:
                return []
            payloads = list(record.edge_payloads or [])
            fetched_at = record.fetched_at.isoformat() + "Z"
            expires_at = record.expires_at.isoformat() + "Z"

        cached_edges: list[TravelEdge] = []
        for index, payload in enumerate(payloads, start=1):
            if not isinstance(payload, dict):
                continue
            restored = dict(payload)
            metadata = dict(restored.get("metadata") or {})
            metadata.update({
                "price_status": "stored_reference_fallback",
                "cache_fetched_at": fetched_at,
                "cache_expires_at": expires_at,
            })
            restored.update({
                "id": f"flight:stored:{origin_iata}:{destination_iata}:{departure_date}:{index}",
                "origin_node_id": origin_node_id,
                "destination_node_id": destination_node_id,
                "metadata": metadata,
            })
            try:
                cached_edges.append(TravelEdge.model_validate(restored))
            except ValueError:
                continue
        return cached_edges


class FlightPriceObservationRepository:
    """Records real provider references and derives cautious route-price ranges."""

    @staticmethod
    def add_edges_in_session(
        session, origin_iata: str, destination_iata: str, departure_date: str,
        edges: list[TravelEdge], observed_at: datetime,
    ) -> None:
        """Append valid provider prices without treating them as current bookable fares."""
        try:
            trip_date = date.fromisoformat(departure_date)
        except ValueError:
            return
        lead_time_days = max(0, (trip_date - observed_at.date()).days)
        for edge in edges:
            if edge.estimated_price_eur <= 0:
                continue
            session.add(FlightPriceObservationRecord(
                origin_iata=origin_iata,
                destination_iata=destination_iata,
                departure_date=departure_date,
                departure_month=trip_date.month,
                departure_weekday=trip_date.weekday(),
                lead_time_days=lead_time_days,
                price_eur=edge.estimated_price_eur,
                transfer_count=_edge_transfer_count(edge),
                duration_minutes=edge.estimated_duration_minutes,
                provider=edge.provider,
                observed_at=observed_at,
            ))

    def get_estimate(self, origin_iata: str, destination_iata: str, departure_date: str) -> dict | None:
        """Return a labelled historic range, favouring observations for the same month and weekday."""
        try:
            trip_date = date.fromisoformat(departure_date)
        except ValueError:
            return None
        with SessionLocal() as session:
            observations = list(session.scalars(
                select(FlightPriceObservationRecord).where(
                    FlightPriceObservationRecord.origin_iata == origin_iata,
                    FlightPriceObservationRecord.destination_iata == destination_iata,
                    FlightPriceObservationRecord.departure_month == trip_date.month,
                    FlightPriceObservationRecord.departure_weekday == trip_date.weekday(),
                ).order_by(FlightPriceObservationRecord.observed_at.desc())
            ))
            if not observations:
                observations = list(session.scalars(
                    select(FlightPriceObservationRecord).where(
                        FlightPriceObservationRecord.origin_iata == origin_iata,
                        FlightPriceObservationRecord.destination_iata == destination_iata,
                    ).order_by(FlightPriceObservationRecord.observed_at.desc())
                ))
        if not observations:
            return None
        prices = sorted(item.price_eur for item in observations)
        count = len(prices)
        confidence = "low" if count < 5 else "medium" if count < 20 else "high"
        return {
            "kind": "historic_estimate",
            "currency": "EUR",
            "low_eur": _percentile(prices, 0.25),
            "typical_eur": _percentile(prices, 0.5),
            "high_eur": _percentile(prices, 0.75),
            "observation_count": count,
            "confidence": confidence,
            "latest_observed_at": observations[0].observed_at.isoformat() + "Z",
            "disclaimer": "Historic provider observations are an estimate, not a live or bookable fare.",
        }


class ScheduledFlightRepository:
    """Imports and exposes schedules separately from volatile flight-price data."""

    def upsert_many(self, flights: list[dict]) -> tuple[int, int]:
        """Insert or refresh imported scheduled flights and return inserted/updated counts."""
        inserted = 0
        updated = 0
        now = _utc_now()
        with SessionLocal() as session:
            for flight in flights:
                existing = session.scalar(select(ScheduledFlightRecord).where(
                    ScheduledFlightRecord.origin_iata == flight["origin_iata"],
                    ScheduledFlightRecord.destination_iata == flight["destination_iata"],
                    ScheduledFlightRecord.service_date == flight["service_date"],
                    ScheduledFlightRecord.planned_departure == flight["planned_departure"],
                    ScheduledFlightRecord.flight_number == flight["flight_number"],
                    ScheduledFlightRecord.source == flight["source"],
                ))
                if existing is None:
                    existing = ScheduledFlightRecord(**flight, imported_at=now)
                    session.add(existing)
                    inserted += 1
                else:
                    for key, value in flight.items():
                        setattr(existing, key, value)
                    existing.imported_at = now
                    updated += 1
            session.commit()
        return inserted, updated

    def get_direct_flights(
        self, origin_iata: str, destination_iata: str, service_date: str,
        source: str | None = None,
    ) -> list[dict]:
        """Return direct schedule rows that are still inside their permitted cache life."""
        now = _utc_now()
        with SessionLocal() as session:
            statement = select(ScheduledFlightRecord).where(
                ScheduledFlightRecord.origin_iata == origin_iata,
                ScheduledFlightRecord.destination_iata == destination_iata,
                ScheduledFlightRecord.service_date == service_date,
                ScheduledFlightRecord.imported_at > now - timedelta(days=SCHEDULE_CACHE_TTL_DAYS),
            )
            if source:
                statement = statement.where(ScheduledFlightRecord.source == source)
            rows = list(session.scalars(statement.order_by(ScheduledFlightRecord.planned_departure)))
        return [{
            "origin_iata": row.origin_iata,
            "destination_iata": row.destination_iata,
            "service_date": row.service_date,
            "planned_departure": row.planned_departure,
            "planned_arrival": row.planned_arrival,
            "flight_number": row.flight_number,
            "airline_code": row.airline_code,
            "airline_name": row.airline_name,
            "source": row.source,
            "source_url": row.source_url,
            "source_updated_at": row.source_updated_at.isoformat() + "Z" if row.source_updated_at else None,
            "imported_at": row.imported_at.isoformat() + "Z",
        } for row in rows]

    def refresh_is_due(self, origin_iata: str, service_date: str, source: str) -> bool:
        """Return whether the airport/day schedule has no valid cached refresh."""
        now = _utc_now()
        with SessionLocal() as session:
            record = session.scalar(select(AirportScheduleFetchRecord).where(
                AirportScheduleFetchRecord.origin_iata == origin_iata,
                AirportScheduleFetchRecord.service_date == service_date,
                AirportScheduleFetchRecord.source == source,
                AirportScheduleFetchRecord.expires_at > now,
            ))
        return record is None

    def mark_refreshed(self, origin_iata: str, service_date: str, source: str) -> None:
        """Mark even an empty successful provider response as cached for seven days."""
        now = _utc_now()
        with SessionLocal() as session:
            record = session.scalar(select(AirportScheduleFetchRecord).where(
                AirportScheduleFetchRecord.origin_iata == origin_iata,
                AirportScheduleFetchRecord.service_date == service_date,
                AirportScheduleFetchRecord.source == source,
            ))
            if record is None:
                record = AirportScheduleFetchRecord(
                    origin_iata=origin_iata,
                    service_date=service_date,
                    source=source,
                    fetched_at=now,
                    expires_at=now + timedelta(days=SCHEDULE_CACHE_TTL_DAYS),
                )
                session.add(record)
            else:
                record.fetched_at = now
                record.expires_at = now + timedelta(days=SCHEDULE_CACHE_TTL_DAYS)
            session.commit()


def _edge_transfer_count(edge: TravelEdge) -> int | None:
    """Extract the provider-reported stop count when it is available on a flight edge."""
    value = edge.metadata.get("transfer_count", edge.metadata.get("transfers"))
    return value if isinstance(value, int) and value >= 0 else None


def _percentile(values: list[float], fraction: float) -> float:
    """Return a simple deterministic percentile without introducing a numeric dependency."""
    if len(values) == 1:
        return round(values[0], 2)
    position = (len(values) - 1) * fraction
    lower = int(position)
    upper = min(lower + 1, len(values) - 1)
    weight = position - lower
    return round(values[lower] * (1 - weight) + values[upper] * weight, 2)


class GatewayAirportRepository:
    """Caches discovered airport gateways independently of short-lived flight prices."""

    def save_candidates(self, origin_city_source_id: str, airports: list[dict]) -> None:
        """Replace candidates after a successful nearby-airport discovery pass."""
        if not airports:
            return
        now = _utc_now()
        with SessionLocal() as session:
            record = session.scalar(
                select(GatewayAirportRecord).where(
                    GatewayAirportRecord.origin_city_source_id == origin_city_source_id
                )
            )
            if record is None:
                record = GatewayAirportRecord(
                    origin_city_source_id=origin_city_source_id,
                    airport_payloads=[],
                    fetched_at=now,
                    expires_at=now,
                )
                session.add(record)
            record.airport_payloads = airports
            record.fetched_at = now
            record.expires_at = now + timedelta(days=GATEWAY_AIRPORT_CACHE_TTL_DAYS)
            session.commit()

    def get_candidates(self, origin_city_source_id: str) -> list[dict]:
        """Return recent gateway airport candidates for a known origin city."""
        now = _utc_now()
        with SessionLocal() as session:
            record = session.scalar(
                select(GatewayAirportRecord).where(
                    GatewayAirportRecord.origin_city_source_id == origin_city_source_id,
                    GatewayAirportRecord.expires_at > now,
                )
            )
            if record is None:
                return []
            return [airport for airport in record.airport_payloads if isinstance(airport, dict)]
