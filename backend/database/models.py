"""SQLAlchemy models used to cache city infrastructure and selected nodes."""

from datetime import datetime

from sqlalchemy import Boolean, JSON, DateTime, Float, ForeignKey, Integer, String, UniqueConstraint
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


class RailPriceObservationRecord(Base):
    """Stores manually collected rail-ticket prices for future estimate calibration.

    These records are deliberately separate from GTFS timetable data.  A fare
    normally applies to a complete itinerary, rather than to a single train
    leg, so the origin, destination and itinerary summary are saved together.
    The route planner does not read this table yet; it is a curated dataset
    prepared for a later rail-price estimation feature.
    """

    __tablename__ = "rail_price_observations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    origin_station_name: Mapped[str] = mapped_column(String(255), index=True)
    origin_station_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    destination_station_name: Mapped[str] = mapped_column(String(255), index=True)
    destination_station_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    travel_date: Mapped[str] = mapped_column(String(10), index=True)
    departure_time: Mapped[str | None] = mapped_column(String(8), nullable=True)
    arrival_time: Mapped[str | None] = mapped_column(String(8), nullable=True)
    search_date: Mapped[str] = mapped_column(String(10), index=True)
    booking_days_ahead: Mapped[int] = mapped_column(Integer, index=True)
    passengers: Mapped[int] = mapped_column(Integer, default=1)
    travel_class: Mapped[str] = mapped_column(String(20), default="2")
    discount_profile: Mapped[str] = mapped_column(String(100), default="none")
    has_deutschlandticket: Mapped[bool] = mapped_column(Boolean, default=False)
    fare_type: Mapped[str] = mapped_column(String(100), default="lowest_available")
    price_eur: Mapped[float] = mapped_column(Float)
    price_min_eur: Mapped[float | None] = mapped_column(Float, nullable=True)
    price_max_eur: Mapped[float | None] = mapped_column(Float, nullable=True)
    currency: Mapped[str] = mapped_column(String(3), default="EUR")
    provider: Mapped[str] = mapped_column(String(255))
    provider_url: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    train_types: Mapped[list] = mapped_column(JSON, default=list)
    itinerary_summary: Mapped[str | None] = mapped_column(String(2000), nullable=True)
    transfer_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    duration_minutes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    is_refundable: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    observation_source: Mapped[str] = mapped_column(String(100), default="manual")
    market: Mapped[str | None] = mapped_column(String(30), nullable=True, index=True)
    service_category: Mapped[str | None] = mapped_column(String(50), nullable=True, index=True)
    provider_context: Mapped[str | None] = mapped_column(String(100), nullable=True, index=True)
    estimated_distance_km: Mapped[float | None] = mapped_column(Float, nullable=True, index=True)
    observed_at: Mapped[datetime] = mapped_column(DateTime, index=True)
    is_example: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    notes: Mapped[str | None] = mapped_column(String(2000), nullable=True)


class RailPriceProfileRecord(Base):
    """Stores a compact, explainable rail-fare estimate rule.

    Profiles deliberately describe a market segment instead of a particular
    city pair.  This allows one calibrated rule to estimate many routes while
    preserving the source and confidence of the underlying assumption.
    """

    __tablename__ = "rail_price_profiles"
    __table_args__ = (
        UniqueConstraint("profile_key", name="uq_rail_price_profile_key"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    profile_key: Mapped[str] = mapped_column(String(150), index=True)
    market: Mapped[str] = mapped_column(String(30), index=True)
    service_category: Mapped[str] = mapped_column(String(50), index=True)
    provider_context: Mapped[str] = mapped_column(String(100), default="any", index=True)
    distance_min_km: Mapped[float] = mapped_column(Float)
    distance_max_km: Mapped[float] = mapped_column(Float)
    booking_days_min: Mapped[int] = mapped_column(Integer)
    booking_days_max: Mapped[int] = mapped_column(Integer)
    price_min_eur: Mapped[float] = mapped_column(Float)
    price_typical_eur: Mapped[float] = mapped_column(Float)
    price_max_eur: Mapped[float] = mapped_column(Float)
    confidence: Mapped[str] = mapped_column(String(20), default="low")
    sample_size: Mapped[int | None] = mapped_column(Integer, nullable=True)
    source: Mapped[str] = mapped_column(String(255))
    is_prototype: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    notes: Mapped[str | None] = mapped_column(String(2000), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, index=True)


class RailStationRecord(Base):
    """Represents one physical station independently from a timetable provider.

    A station is deliberately not tied to an OSM node or a GTFS stop identifier.
    Different national timetable feeds frequently use different identifiers for
    the same building.  This table is the stable identity that lets TRO join
    those networks through explicit, inspectable station memberships.
    """

    __tablename__ = "rail_stations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    canonical_id: Mapped[str] = mapped_column(String(300), unique=True, index=True)
    home_region: Mapped[str] = mapped_column(String(30), index=True)
    name: Mapped[str] = mapped_column(String(500))
    normalized_name: Mapped[str] = mapped_column(String(500), index=True)
    latitude: Mapped[float] = mapped_column(Float)
    longitude: Mapped[float] = mapped_column(Float)
    created_at: Mapped[datetime] = mapped_column(DateTime)
    updated_at: Mapped[datetime] = mapped_column(DateTime, index=True)


class RailStationMembershipRecord(Base):
    """Maps one canonical station to the identifier used by one rail network."""

    __tablename__ = "rail_station_memberships"
    __table_args__ = (
        UniqueConstraint("network_key", "source_stop_id", name="uq_rail_station_membership"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    station_id: Mapped[int] = mapped_column(ForeignKey("rail_stations.id", ondelete="CASCADE"), index=True)
    network_key: Mapped[str] = mapped_column(String(100), index=True)
    source_stop_id: Mapped[str] = mapped_column(String(255))
    source_name: Mapped[str] = mapped_column(String(500))
    latitude: Mapped[float] = mapped_column(Float)
    longitude: Mapped[float] = mapped_column(Float)
    is_parent_station: Mapped[bool] = mapped_column(Boolean, default=True)
    service_score: Mapped[int] = mapped_column(Integer, default=1)
    imported_at: Mapped[datetime] = mapped_column(DateTime, index=True)


class RailStationInterchangeRecord(Base):
    """Stores an approved transfer between two networks at one physical station.

    The index is intentionally directional.  Future countries may need a
    different minimum change time in each direction or a walk to another
    station, without changing the identity of either station.
    """

    __tablename__ = "rail_station_interchanges"
    __table_args__ = (
        UniqueConstraint(
            "station_id", "from_network_key", "to_network_key",
            name="uq_rail_station_interchange",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    station_id: Mapped[int] = mapped_column(ForeignKey("rail_stations.id", ondelete="CASCADE"), index=True)
    from_network_key: Mapped[str] = mapped_column(String(100), index=True)
    to_network_key: Mapped[str] = mapped_column(String(100), index=True)
    minimum_transfer_minutes: Mapped[int] = mapped_column(Integer, default=15)
    transfer_kind: Mapped[str] = mapped_column(String(50), default="same_station")
    imported_at: Mapped[datetime] = mapped_column(DateTime, index=True)


class RailNetworkImportRecord(Base):
    """Audits the GTFS archive used to build a modular rail-network index."""

    __tablename__ = "rail_network_imports"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    network_key: Mapped[str] = mapped_column(String(100), unique=True, index=True)
    archive_path: Mapped[str] = mapped_column(String(1000))
    archive_fingerprint: Mapped[str] = mapped_column(String(128), index=True)
    parent_station_count: Mapped[int] = mapped_column(Integer)
    imported_at: Mapped[datetime] = mapped_column(DateTime, index=True)
