"""SQLAlchemy models used to cache city infrastructure and selected nodes."""

from datetime import datetime

from sqlalchemy import DateTime, Float, ForeignKey, Integer, String, UniqueConstraint
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
