"""Read cached flight reference prices from the Travelpayouts data API."""

import math
import os
from datetime import date

import httpx
from dotenv import load_dotenv

from backend.models.graph import TravelEdge
from backend.models.node import TransportNode

load_dotenv()

TRAVELPAYOUTS_CHEAP_PRICES_URL = "https://api.travelpayouts.com/v1/prices/cheap"
TRAVELPAYOUTS_PROVIDER_NAME = "Travelpayouts Aviasales Data API"


class FlightProviderUnavailableError(Exception):
    """Raised when the configured flight-data provider cannot be reached."""


def _airport_iata(node: TransportNode) -> str | None:
    """Return a normalized IATA code only for an airport graph node."""
    if node.type != "airport":
        return None
    iata = node.metadata.get("iata")
    return iata.upper() if isinstance(iata, str) and len(iata) == 3 else None


def _great_circle_distance_km(origin: TransportNode, destination: TransportNode) -> float:
    """Calculate airport distance used for a clearly labelled duration estimate."""
    earth_radius_km = 6_371.0088
    latitude_delta = math.radians(destination.latitude - origin.latitude)
    longitude_delta = math.radians(destination.longitude - origin.longitude)
    haversine = (
        math.sin(latitude_delta / 2) ** 2
        + math.cos(math.radians(origin.latitude))
        * math.cos(math.radians(destination.latitude))
        * math.sin(longitude_delta / 2) ** 2
    )
    return earth_radius_km * 2 * math.atan2(math.sqrt(haversine), math.sqrt(1 - haversine))


def _estimated_flight_duration_minutes(distance_km: float) -> int:
    """Estimate gate-to-gate duration until a timetable provider supplies schedules."""
    cruise_minutes = (distance_km / 800) * 60
    return max(45, round(cruise_minutes + 75))


class TravelpayoutsFlightProvider:
    """Fetch route-specific cached offers without ever exposing the API token."""

    def __init__(self, token: str | None = None) -> None:
        """Use an injected token for tests or the local environment token in production."""
        self._token = token if token is not None else os.getenv("TRAVELPAYOUTS_API_TOKEN")

    @property
    def configured(self) -> bool:
        """Report whether this environment can query the Flight Data API."""
        return bool(self._token)

    async def find_connection_edges(
        self,
        origin: TransportNode,
        destination: TransportNode,
        departure_date: str,
    ) -> list[TravelEdge]:
        """Create flight edges from the cheapest cached offers for one airport pair.

        Travelpayouts returns cached reference prices rather than bookable, live offers.
        Duration is an estimate because this endpoint lacks an arrival timestamp.
        """
        origin_iata = _airport_iata(origin)
        destination_iata = _airport_iata(destination)
        if not self.configured or not origin_iata or not destination_iata:
            return []
        try:
            date.fromisoformat(departure_date)
        except ValueError:
            return []

        headers = {
            "X-Access-Token": self._token,
            "User-Agent": "TRO-Travel-Route-Optimizer/0.1",
        }
        params = {
            "origin": origin_iata,
            "destination": destination_iata,
            "depart_date": departure_date,
            "currency": "EUR",
        }
        try:
            async with httpx.AsyncClient(timeout=15) as client:
                response = await client.get(
                    TRAVELPAYOUTS_CHEAP_PRICES_URL, params=params, headers=headers
                )
                response.raise_for_status()
                payload = response.json()
        except (httpx.HTTPError, ValueError) as error:
            raise FlightProviderUnavailableError(
                "Travelpayouts flight data is temporarily unavailable."
            ) from error

        offers = payload.get("data", {}).get(destination_iata, {})
        if not isinstance(offers, dict):
            return []

        distance_km = round(_great_circle_distance_km(origin, destination), 2)
        duration_minutes = _estimated_flight_duration_minutes(distance_km)
        edges: list[TravelEdge] = []
        for offer_key, offer in offers.items():
            if not isinstance(offer, dict) or not isinstance(offer.get("price"), (int, float)):
                continue
            departure_at = offer.get("departure_at")
            if isinstance(departure_at, str) and not departure_at.startswith(departure_date):
                continue
            flight_number = str(offer.get("flight_number") or "unknown")
            edges.append(
                TravelEdge(
                    id=f"flight:travelpayouts:{origin_iata}:{destination_iata}:{offer_key}:{flight_number}",
                    origin_node_id=origin.id,
                    destination_node_id=destination.id,
                    mode="flight",
                    edge_type="flight_connection",
                    estimated_distance_km=distance_km,
                    estimated_duration_minutes=duration_minutes,
                    estimated_price_eur=round(float(offer["price"]), 2),
                    provider=TRAVELPAYOUTS_PROVIDER_NAME,
                    metadata={
                        "origin_iata": origin_iata,
                        "destination_iata": destination_iata,
                        "airline": offer.get("airline"),
                        "flight_number": offer.get("flight_number"),
                        "departure_at": departure_at,
                        "price_status": "cached_reference",
                        "price_currency": "EUR",
                        "price_expires_at": offer.get("expires_at"),
                        "duration_status": "TRO distance estimate",
                    },
                )
            )
        return sorted(edges, key=lambda edge: edge.estimated_price_eur)


async def discover_flight_edges(
    origin_nodes: list[TransportNode],
    destination_nodes: list[TransportNode],
    departure_date: str,
) -> tuple[list[TravelEdge], list[dict[str, str]]]:
    """Query eligible airport pairs and return usable edges plus safe warnings."""
    provider = TravelpayoutsFlightProvider()
    if not provider.configured:
        return [], [{
            "infrastructure_type": "flights",
            "message": "Flight reference prices are not configured on this server.",
        }]

    origin_airports = [node for node in origin_nodes if _airport_iata(node)]
    destination_airports = [node for node in destination_nodes if _airport_iata(node)]
    edges: list[TravelEdge] = []
    warnings: list[dict[str, str]] = []
    for origin_airport in origin_airports:
        for destination_airport in destination_airports:
            try:
                edges.extend(
                    await provider.find_connection_edges(
                        origin_airport, destination_airport, departure_date
                    )
                )
            except FlightProviderUnavailableError:
                warnings.append({
                    "infrastructure_type": "flights",
                    "message": "Flight reference prices are temporarily unavailable; car routes remain available.",
                })
    return edges, warnings
