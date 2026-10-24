"""Schemas accepted by graph-discovery routes."""

from pydantic import BaseModel, Field
from backend.models.graph import RouteRanking


class SearchRequest(BaseModel):
    """Defines the user input required to discover graph nodes for a journey."""

    origin: str = Field(min_length=2, max_length=120, description="Origin city used to create initial graph nodes.", examples=["Erfurt"])
    destination: str = Field(min_length=2, max_length=120, description="Destination city used to create final graph nodes.", examples=["München"])
    date: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$", description="Planned travel date in YYYY-MM-DD format.", examples=["2026-10-10"])
    departure_time: str = Field(default="08:00", pattern=r"^([01]\d|2[0-3]):[0-5]\d$", description="Local departure time in Europe/Berlin (HH:MM). It is used when `respect_departure_time` is true.", examples=["08:00"])
    respect_departure_time: bool = Field(default=True, description="When true, reject a timed connection that leaves before the traveller can reach it. When false, compare available options across the selected day without treating departure_time as a constraint.")
    has_deutschlandticket: bool = Field(default=False, description="Whether every traveller in this search already has a valid Deutschlandticket.")
    travellers: int = Field(default=1, ge=1, le=9, description="Number of travellers. This will affect paid transport prices later.")
    ranking_criterion: RouteRanking = Field(default="lowest_price", description="Rank all complete routes before selecting ten: lowest_price, fastest (including waits), or balanced (equal weight for normalized price and total journey time).")
    max_transport_categories: int = Field(default=4, ge=1, le=6, description="Maximum number of transport categories. Consecutive train legs count as one category.")
    minimum_train_transfer_minutes: int = Field(default=0, ge=0, le=60, description="Minimum scheduled waiting time required for every train-to-train transfer. Zero keeps all available connections.")
    include_diagnostics: bool = Field(default=False, description="Return read-only counts and rejection reasons for each rail-search stage. This does not bypass the cache or refresh external city infrastructure.")
    debug: bool = Field(default=False, description="Bypass the database cache and return filter diagnostics.")
