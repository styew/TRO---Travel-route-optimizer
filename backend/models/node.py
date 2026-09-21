# Here creat the Object for Graph
# Every Node is a city and Edge the connection between citys for time/prise

"""Data model for a long-distance transport candidate in the route graph."""

from typing import Any, Literal

from pydantic import BaseModel, Field


class TransportNode(BaseModel):
    """Represents a city center, train station, or airport used by the graph."""

    id: str
    type: Literal["city_center", "train_station", "airport"]
    name: str
    latitude: float
    longitude: float
    supported_modes: list[Literal["car", "train", "flight"]]
    source: str
    source_id: str
    metadata: dict[str, Any] = Field(default_factory=dict)
