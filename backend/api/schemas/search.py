"""Schemas accepted by graph-discovery routes."""

from pydantic import BaseModel, Field


class SearchRequest(BaseModel):
    """Defines the user input required to discover graph nodes for a journey."""

    origin: str = Field(min_length=2, description="Origin city used to create initial graph nodes.", examples=["Erfurt"])
    destination: str = Field(min_length=2, description="Destination city used to create final graph nodes.", examples=["München"])
    date: str = Field(description="Planned travel date in YYYY-MM-DD format.", examples=["2026-10-10"])
    has_deutschlandticket: bool = Field(default=False, description="Whether every traveller in this search already has a valid Deutschlandticket.")
    travellers: int = Field(default=1, ge=1, le=9, description="Number of travellers. This will affect paid transport prices later.")
    max_transport_categories: int = Field(default=4, ge=1, le=6, description="Maximum number of transport categories. Consecutive train legs count as one category.")
    debug: bool = Field(default=False, description="Bypass the database cache and return filter diagnostics.")
