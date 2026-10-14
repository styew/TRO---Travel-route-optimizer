"""Data models returned while the route graph is assembled incrementally."""

from typing import Any, Literal

from pydantic import BaseModel, Field

from backend.models.node import TransportNode


class GraphPolicy(BaseModel):
    """Defines conservative limits for the first car-based graph expansion."""

    max_gateway_stations: int = Field(default=3, ge=1, le=5)
    max_segments: int = Field(default=4, ge=1, le=8)
    max_car_access_minutes: int = Field(default=90, ge=15, le=180)
    estimated_car_speed_kmh: float = Field(default=70.0, gt=0)
    estimated_road_distance_factor: float = Field(default=1.25, ge=1.0, le=2.0)
    estimated_car_cost_per_hour_eur: float = Field(default=12.0, ge=0)


class TravelEdge(BaseModel):
    """Represents one traversable connection between two graph nodes."""

    id: str
    origin_node_id: str
    destination_node_id: str
    mode: Literal["car", "train", "flight", "ferry"]
    edge_type: Literal[
        "direct_car",
        "car_access_to_station",
        "train_connection",
        "flight_connection",
        "ferry_connection",
        "verified_transfer",
    ]
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


class RouteOption(BaseModel):
    """One complete traversable path ranked by its total estimated cost."""

    rank: int
    node_path: list[str]
    segments: list[TravelEdge]
    total_estimated_price_eur: float
    total_estimated_duration_minutes: int
    total_estimated_distance_km: float
    transfer_count: int


class RoutePlan(BaseModel):
    """Cheapest complete route options currently available in a route graph."""

    ranking_criterion: Literal["lowest_price"] = "lowest_price"
    max_segments: int
    routes: list[RouteOption]
