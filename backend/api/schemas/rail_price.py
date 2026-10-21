"""Schemas used to inspect the transparent prototype rail-price estimator."""

from typing import Literal

from pydantic import BaseModel, Field


class RailPriceEstimateRequest(BaseModel):
    """Defines the route characteristics required by the profile-based estimator."""

    market: str = Field(default="DE", min_length=2, max_length=30, examples=["DE"])
    service_category: Literal["regional", "long_distance", "night", "international"] = Field(
        examples=["long_distance"]
    )
    distance_km: float = Field(gt=0, examples=[505])
    booking_days_ahead: int = Field(ge=0, le=365, examples=[7])
    provider_context: str | None = Field(
        default=None,
        min_length=2,
        max_length=100,
        examples=["db"],
        description="Optional seller or operator context, such as db or oebb.",
    )
    passengers: int = Field(default=1, ge=1, le=9)
    has_deutschlandticket: bool = Field(default=False)


class RailPriceEstimateResponse(BaseModel):
    """Returns a labelled estimate range or explains that no profile covers the request."""

    estimate_available: bool
    estimated_min_eur: float | None = None
    estimated_typical_eur: float | None = None
    estimated_max_eur: float | None = None
    confidence: str | None = None
    sample_size: int | None = None
    price_status: str | None = None
    profile_key: str | None = None
    source: str | None = None
    disclaimer: str
