"""Data models returned while the route graph is assembled incrementally."""

from typing import Any, Literal

from pydantic import BaseModel, Field

from backend.models.node import TransportNode


class GraphPolicy(BaseModel):
    """Defines conservative limits for the first car-based graph expansion."""

    max_gateway_stations: int = Field(default=3, ge=1, le=5)
    max_car_access_minutes: int = Field(default=90, ge=15, le=180)
    estimated_car_speed_kmh: float = Field(default=70.0, gt=0)
    estimated_road_distance_factor: float = Field(default=1.25, ge=1.0, le=2.0)
    estimated_car_cost_per_hour_eur: float = Field(default=12.0, ge=0)


class TravelEdge(BaseModel):
    """Represents one traversable connection between two graph nodes."""

    id: str
    origin_node_id: str
    destination_node_id: str
    mode: Literal["car", "train", "flight"]
    edge_type: Literal["direct_car", "car_access_to_station"]
    estimated_distance_km: float
    estimated_duration_minutes: int
    estimated_price_eur: float
    is_estimate: bool = True
    provider: str
    booking_url: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class RouteGraph(BaseModel):
    """Serializable graph structure that a later frontend may render in 2D or 3D."""

    nodes: list[TransportNode]
    edges: list[TravelEdge]
    origin_node_ids: list[str]
    destination_node_ids: list[str]
    policy: GraphPolicy
