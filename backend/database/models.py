"""SQLAlchemy models used to cache city infrastructure and selected nodes."""

from datetime import datetime

from sqlalchemy import JSON, DateTime, Float, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from backend.database.session import Base


class CityRecord(Base):
    """Stores the canonical city result returned by the geocoding provider."""

    __tablename__ = "cities"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    source_id: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    city_name: Mapped[str] = mapped_column(String(255))
    display_name: Mapped[str] = mapped_column(String(500))
    latitude: Mapped[float] = mapped_column(Float)
    longitude: Mapped[float] = mapped_column(Float)
    source: Mapped[str] = mapped_column(String(100))
    infrastructure_filter_version: Mapped[int] = mapped_column(Integer, default=1)
    fetched_at: Mapped[datetime] = mapped_column(DateTime)
    expires_at: Mapped[datetime] = mapped_column(DateTime, index=True)


class TransportNodeRecord(Base):
    """Stores a normalized railway station or airport returned by infrastructure APIs."""

    __tablename__ = "transport_nodes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    source_id: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    node_type: Mapped[str] = mapped_column(String(50), index=True)
    name: Mapped[str] = mapped_column(String(500))
    latitude: Mapped[float] = mapped_column(Float)
    longitude: Mapped[float] = mapped_column(Float)
    iata: Mapped[str | None] = mapped_column(String(20), nullable=True)
    icao: Mapped[str | None] = mapped_column(String(20), nullable=True)
    source: Mapped[str] = mapped_column(String(100))
    updated_at: Mapped[datetime] = mapped_column(DateTime)


class CityNodeCandidateRecord(Base):
    """Links a city to a selected long-distance transport node and its filter decision."""

    __tablename__ = "city_node_candidates"
    __table_args__ = (
        UniqueConstraint("city_id", "transport_node_id", name="uq_city_node_candidate"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    city_id: Mapped[int] = mapped_column(ForeignKey("cities.id", ondelete="CASCADE"))
    transport_node_id: Mapped[int] = mapped_column(
        ForeignKey("transport_nodes.id", ondelete="CASCADE")
    )
    distance_km: Mapped[float] = mapped_column(Float)
    selected: Mapped[bool] = mapped_column(default=True)
    selection_rule: Mapped[str] = mapped_column(String(100))
    fetched_at: Mapped[datetime] = mapped_column(DateTime)


class FlightReferenceRecord(Base):
    """Stores recent provider flight references for a specific airport pair and date."""

    __tablename__ = "flight_references"
    __table_args__ = (
        UniqueConstraint(
            "origin_iata", "destination_iata", "departure_date",
            name="uq_flight_reference_pair_date",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    origin_iata: Mapped[str] = mapped_column(String(3), index=True)
    destination_iata: Mapped[str] = mapped_column(String(3), index=True)
    departure_date: Mapped[str] = mapped_column(String(10), index=True)
    edge_payloads: Mapped[list] = mapped_column(JSON)
    fetched_at: Mapped[datetime] = mapped_column(DateTime)
    expires_at: Mapped[datetime] = mapped_column(DateTime, index=True)


class GatewayAirportRecord(Base):
    """Stores bounded departure-airport candidates found near one origin city."""

    __tablename__ = "gateway_airports"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    origin_city_source_id: Mapped[str] = mapped_column(
        String(255), unique=True, index=True
    )
    airport_payloads: Mapped[list] = mapped_column(JSON)
    fetched_at: Mapped[datetime] = mapped_column(DateTime)
    expires_at: Mapped[datetime] = mapped_column(DateTime, index=True)


class ScheduledFlightRecord(Base):
    """Stores one published flight movement imported from an authorised schedule source."""

    __tablename__ = "scheduled_flights"
    __table_args__ = (
        UniqueConstraint(
            "origin_iata", "destination_iata", "service_date", "planned_departure",
            "flight_number", "source", name="uq_scheduled_flight_service",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    origin_iata: Mapped[str] = mapped_column(String(3), index=True)
    destination_iata: Mapped[str] = mapped_column(String(3), index=True)
    service_date: Mapped[str] = mapped_column(String(10), index=True)
    planned_departure: Mapped[str] = mapped_column(String(40), index=True)
    planned_arrival: Mapped[str] = mapped_column(String(40))
    flight_number: Mapped[str] = mapped_column(String(20), index=True)
    airline_code: Mapped[str | None] = mapped_column(String(3), nullable=True)
    airline_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    source: Mapped[str] = mapped_column(String(255))
    source_url: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    source_updated_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    imported_at: Mapped[datetime] = mapped_column(DateTime)


class AirportScheduleFetchRecord(Base):
    """Records a successful airport/day schedule refresh, including empty results.

    Keeping this independently from ``ScheduledFlightRecord`` prevents a route
    with no matching flight from consuming an AeroDataBox request on every
    user search.
    """

    __tablename__ = "airport_schedule_fetches"
    __table_args__ = (
        UniqueConstraint(
            "origin_iata", "service_date", "source",
            name="uq_airport_schedule_fetch",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    origin_iata: Mapped[str] = mapped_column(String(3), index=True)
    service_date: Mapped[str] = mapped_column(String(10), index=True)
    source: Mapped[str] = mapped_column(String(255))
    fetched_at: Mapped[datetime] = mapped_column(DateTime)
    expires_at: Mapped[datetime] = mapped_column(DateTime, index=True)


class FlightPriceObservationRecord(Base):
    """Keeps dated provider price observations for transparent future estimates."""

    __tablename__ = "flight_price_observations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    origin_iata: Mapped[str] = mapped_column(String(3), index=True)
    destination_iata: Mapped[str] = mapped_column(String(3), index=True)
    departure_date: Mapped[str] = mapped_column(String(10), index=True)
    departure_month: Mapped[int] = mapped_column(Integer, index=True)
    departure_weekday: Mapped[int] = mapped_column(Integer, index=True)
    lead_time_days: Mapped[int] = mapped_column(Integer, index=True)
    price_eur: Mapped[float] = mapped_column(Float)
    transfer_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    duration_minutes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    provider: Mapped[str] = mapped_column(String(255))
    observed_at: Mapped[datetime] = mapped_column(DateTime, index=True)
