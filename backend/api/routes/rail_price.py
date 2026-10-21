"""Expose the rail-price prototype for transparent testing before graph integration."""

from fastapi import APIRouter

from backend.api.schemas.rail_price import RailPriceEstimateRequest, RailPriceEstimateResponse
from backend.services.rail_price_estimator import rail_price_estimator


router = APIRouter(tags=["Rail price estimates"])


@router.post(
    "/rail-price-estimate",
    summary="Estimate a rail-ticket price range from a documented profile",
    description=(
        "Returns a transparent prototype estimate for one complete rail itinerary. "
        "It does not query a ticket seller, confirm availability, or claim a live fare. "
        "The initial profiles cover only a narrow German long-distance pilot range."
    ),
    response_description="A labelled price range or a statement that no profile covers the request.",
)
async def estimate_rail_price(request: RailPriceEstimateRequest) -> RailPriceEstimateResponse:
    """Return a profile-based estimate without creating or changing a route graph."""
    estimate = rail_price_estimator.estimate(
        market=request.market,
        service_category=request.service_category,
        distance_km=request.distance_km,
        booking_days_ahead=request.booking_days_ahead,
        provider_context=request.provider_context,
        passengers=request.passengers,
        has_deutschlandticket=request.has_deutschlandticket,
    )
    if estimate is None:
        return RailPriceEstimateResponse(
            estimate_available=False,
            disclaimer=(
                "No calibrated profile covers this market, service category, "
                "distance, and booking horizon yet."
            ),
        )
    return RailPriceEstimateResponse(estimate_available=True, **estimate.as_dict())
