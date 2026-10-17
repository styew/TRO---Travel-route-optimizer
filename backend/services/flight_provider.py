"""Read cached flight reference prices from the Travelpayouts data API."""

import math
import os
from datetime import date, datetime, timedelta

import httpx
from dotenv import load_dotenv
from sqlalchemy.exc import SQLAlchemyError

from backend.database.repository import FlightReferenceRepository
from backend.models.graph import TravelEdge
from backend.models.node import TransportNode

load_dotenv()

TRAVELPAYOUTS_CHEAP_PRICES_URL = "https://api.travelpayouts.com/v1/prices/cheap"
TRAVELPAYOUTS_CALENDAR_PRICES_URL = "https://api.travelpayouts.com/v1/prices/calendar"
TRAVELPAYOUTS_DATE_PRICES_URL = "https://api.travelpayouts.com/aviasales/v3/prices_for_dates"
TRAVELPAYOUTS_PROVIDER_NAME = "Travelpayouts Aviasales Data API"
DUFFEL_OFFER_REQUESTS_URL = "https://api.duffel.com/air/offer_requests"
DUFFEL_PROVIDER_NAME = "Duffel live flight offers"


class FlightProviderUnavailableError(Exception):
    """Raised when the configured flight-data provider cannot be reached."""


def _duration_from_iso8601(value: object) -> int | None:
    """Convert a simple ISO-8601 duration such as PT2H15M into whole minutes."""
    if not isinstance(value, str) or not value.startswith("P"):
        return None
    hours = minutes = 0
    try:
        time_part = value.split("T", maxsplit=1)[1]
        if "H" in time_part:
            hours = int(time_part.split("H", maxsplit=1)[0])
            time_part = time_part.split("H", maxsplit=1)[1]
        if "M" in time_part:
            minutes = int(time_part.split("M", maxsplit=1)[0])
    except (IndexError, ValueError):
        return None
    return hours * 60 + minutes


def _minutes_between(start: object, end: object) -> int | None:
    """Calculate a scheduled interval only when both provider timestamps parse."""
    if not isinstance(start, str) or not isinstance(end, str):
        return None
    try:
        return max(0, round((datetime.fromisoformat(end) - datetime.fromisoformat(start)).total_seconds() / 60))
    except ValueError:
        return None


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


def _normalized_stop_count(value: object) -> int | None:
    """Return a provider stop count only when it is a non-negative whole number."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int) and value >= 0:
        return value
    if isinstance(value, float) and value.is_integer() and value >= 0:
        return int(value)
    return None


def _validated_itinerary_duration(
    provider_duration: object, transfers: object, distance_km: float
) -> tuple[int, bool, str, int | None]:
    """Reject implausible direct-flight duration data while retaining it for audit."""
    estimate = _estimated_flight_duration_minutes(distance_km)
    if not isinstance(provider_duration, (int, float)) or provider_duration <= 0:
        return estimate, False, "TRO distance estimate", None
    reported_duration = round(float(provider_duration))
    stops = _normalized_stop_count(transfers)
    # A direct flight can be delayed, but an itinerary more than twice the
    # geographic gate-to-gate estimate (and over three hours) contradicts a
    # zero-stop record. Do not turn that inconsistent cached field into a
    # misleading route duration or arrival time.
    maximum_direct_duration = max(180, estimate * 2)
    if stops == 0 and reported_duration > maximum_direct_duration:
        return (
            estimate,
            False,
            "Provider duration rejected as inconsistent with zero stops",
            reported_duration,
        )
    return reported_duration, True, "Travelpayouts itinerary duration", reported_duration


def _flight_schedule_metadata(
    departure_at: object, duration_minutes: int | None = None
) -> dict[str, str]:
    """Expose provider schedule times only when an actual timestamp is available."""
    if not isinstance(departure_at, str) or "T" not in departure_at:
        return {}
    try:
        departure = datetime.fromisoformat(departure_at.replace("Z", "+00:00"))
    except ValueError:
        return {}
    schedule = {"planned_departure": departure.isoformat()}
    if duration_minutes is not None:
        schedule["planned_arrival"] = (
            departure + timedelta(minutes=duration_minutes)
        ).isoformat()
    return schedule


def _validate_restored_flight_edge(edge: TravelEdge) -> TravelEdge:
    """Apply current duration safeguards to references stored by older versions."""
    # Older cached Travelpayouts payloads did not expose the stop count to the
    # route planner, so a one-stop flight was incorrectly summarised as direct.
    edge.metadata.setdefault(
        "transfer_count", _normalized_stop_count(edge.metadata.get("transfers")) or 0
    )
    if edge.metadata.get("duration_status") != "Travelpayouts itinerary duration":
        return edge
    duration, is_provider_duration, status, raw_duration = (
        _validated_itinerary_duration(
            edge.estimated_duration_minutes,
            edge.metadata.get("transfers"),
            edge.estimated_distance_km,
        )
    )
    if is_provider_duration:
        return edge
    edge.estimated_duration_minutes = duration
    edge.is_estimate = True
    edge.metadata.update({
        "duration_status": status,
        "provider_duration_minutes": raw_duration,
    })
    edge.metadata.pop("planned_arrival", None)
    return edge


class DuffelFlightProvider:
    """Read live, segment-level flight offers when a Duffel access token exists.

    Unlike a cached fare-feed, Duffel returns every flight segment, its airports,
    operating carrier, and timestamps. Those details let TRO display a genuine
    connection timeline instead of trying to infer a layover from a booking URL.
    """

    def __init__(self, token: str | None = None) -> None:
        """Use an injected token for tests or the local environment token in production."""
        self._token = token if token is not None else os.getenv("DUFFEL_ACCESS_TOKEN")

    @property
    def configured(self) -> bool:
        """Report whether this server can query live, detailed flight offers."""
        return bool(self._token)

    @staticmethod
    def _airport_label(airport: object, fallback_code: str) -> tuple[str, str]:
        """Return a safe airport display name and IATA code from one Duffel object."""
        if not isinstance(airport, dict):
            return fallback_code, fallback_code
        code = airport.get("iata_code")
        name = airport.get("name")
        normalized_code = code.upper() if isinstance(code, str) and len(code) == 3 else fallback_code
        return str(name) if isinstance(name, str) and name else normalized_code, normalized_code

    def _create_edge(
        self, offer: dict, origin: TransportNode, destination: TransportNode,
        departure_date: str, index: int,
    ) -> TravelEdge | None:
        """Normalize one live offer and preserve all segment and layover details."""
        total_amount = offer.get("total_amount")
        total_currency = str(offer.get("total_currency") or "").upper()
        slices = offer.get("slices")
        if (
            not isinstance(total_amount, (str, int, float))
            or total_currency != "EUR"
            or not isinstance(slices, list)
            or not slices
            or not isinstance(slices[0], dict)
        ):
            return None
        try:
            price = round(float(total_amount), 2)
        except (TypeError, ValueError):
            return None
        raw_segments = slices[0].get("segments")
        if not isinstance(raw_segments, list) or not raw_segments:
            return None

        origin_iata = _airport_iata(origin) or ""
        destination_iata = _airport_iata(destination) or ""
        flight_legs: list[dict[str, object]] = []
        transfer_waits: list[dict[str, object]] = []
        for position, raw_segment in enumerate(raw_segments):
            if not isinstance(raw_segment, dict):
                return None
            origin_name, leg_origin_iata = self._airport_label(raw_segment.get("origin"), origin_iata)
            destination_name, leg_destination_iata = self._airport_label(raw_segment.get("destination"), destination_iata)
            carrier = raw_segment.get("operating_carrier")
            carrier = carrier if isinstance(carrier, dict) else {}
            carrier_code = carrier.get("iata_code") if isinstance(carrier.get("iata_code"), str) else None
            flight_number = raw_segment.get("operating_carrier_flight_number")
            if not isinstance(flight_number, str):
                flight_number = raw_segment.get("marketing_carrier_flight_number")
            full_flight_number = f"{carrier_code or ''}{flight_number or ''}" or "Flight number unavailable"
            departing_at = raw_segment.get("departing_at")
            arriving_at = raw_segment.get("arriving_at")
            duration = _duration_from_iso8601(raw_segment.get("duration"))
            if duration is None:
                duration = _minutes_between(departing_at, arriving_at)
            flight_legs.append({
                "from": origin_name,
                "from_iata": leg_origin_iata,
                "to": destination_name,
                "to_iata": leg_destination_iata,
                "scheduled_departure": departing_at,
                "scheduled_arrival": arriving_at,
                "duration_minutes": duration,
                "airline": carrier.get("name") or carrier_code or "Airline unavailable",
                "airline_code": carrier_code,
                "flight_number": full_flight_number,
                "departure_terminal": raw_segment.get("origin_terminal"),
                "arrival_terminal": raw_segment.get("destination_terminal"),
            })
            if position and flight_legs[position - 1].get("scheduled_arrival"):
                previous = flight_legs[position - 1]
                wait_minutes = _minutes_between(previous.get("scheduled_arrival"), departing_at)
                transfer_waits.append({
                    "at_airport": origin_name,
                    "at_airport_iata": leg_origin_iata,
                    "scheduled_arrival": previous.get("scheduled_arrival"),
                    "scheduled_departure": departing_at,
                    "wait_minutes": wait_minutes,
                })

        first_leg, last_leg = flight_legs[0], flight_legs[-1]
        itinerary_duration = _minutes_between(
            first_leg.get("scheduled_departure"), last_leg.get("scheduled_arrival")
        ) or _duration_from_iso8601(slices[0].get("duration"))
        if itinerary_duration is None:
            return None
        offer_id = offer.get("id") if isinstance(offer.get("id"), str) else str(index)
        return TravelEdge(
            id=f"flight:duffel:{origin_iata}:{destination_iata}:{departure_date}:{offer_id}",
            origin_node_id=origin.id,
            destination_node_id=destination.id,
            mode="flight",
            edge_type="flight_connection",
            estimated_distance_km=round(_great_circle_distance_km(origin, destination), 2),
            estimated_duration_minutes=itinerary_duration,
            estimated_price_eur=price,
            is_estimate=False,
            provider=DUFFEL_PROVIDER_NAME,
            metadata={
                "origin_iata": first_leg.get("from_iata") or origin_iata,
                "destination_iata": last_leg.get("to_iata") or destination_iata,
                "airline": first_leg.get("airline"),
                "flight_number": first_leg.get("flight_number"),
                "departure_at": first_leg.get("scheduled_departure"),
                "transfers": len(transfer_waits),
                "transfer_count": len(transfer_waits),
                "transfer_waits": transfer_waits,
                "flight_legs": flight_legs,
                "price_status": "live_offer",
                "price_currency": "EUR",
                "offer_expires_at": offer.get("expires_at"),
                "duration_status": "Live itinerary duration",
                "planned_departure": first_leg.get("scheduled_departure"),
                "planned_arrival": last_leg.get("scheduled_arrival"),
            },
        )

    async def find_connection_edges(
        self, origin: TransportNode, destination: TransportNode, departure_date: str,
        travellers: int,
    ) -> list[TravelEdge]:
        """Request up to two-stop live offers and return their explicit itineraries."""
        origin_iata = _airport_iata(origin)
        destination_iata = _airport_iata(destination)
        if not self.configured or not origin_iata or not destination_iata:
            return []
        passengers = [{"type": "adult"} for _ in range(travellers)]
        request_body = {"data": {
            "slices": [{
                "origin": origin_iata,
                "destination": destination_iata,
                "departure_date": departure_date,
            }],
            "passengers": passengers,
            "cabin_class": "economy",
            "max_connections": 2,
        }}
        try:
            async with httpx.AsyncClient(timeout=30) as client:
                response = await client.post(
                    DUFFEL_OFFER_REQUESTS_URL,
                    params={"return_offers": "true", "supplier_timeout": 20_000},
                    json=request_body,
                    headers={
                        "Accept": "application/json",
                        "Duffel-Version": "v2",
                        "Authorization": f"Bearer {self._token}",
                    },
                )
                response.raise_for_status()
                data = response.json().get("data", {})
        except (httpx.HTTPError, ValueError) as error:
            raise FlightProviderUnavailableError(
                "Duffel live flight offers are temporarily unavailable."
            ) from error
        offers = data.get("offers", []) if isinstance(data, dict) else []
        if not isinstance(offers, list):
            return []
        edges = [
            edge for index, offer in enumerate(offers[:20], start=1)
            if isinstance(offer, dict)
            if (edge := self._create_edge(offer, origin, destination, departure_date, index))
        ]
        return sorted(edges, key=lambda edge: edge.estimated_price_eur)


class TravelpayoutsFlightProvider:
    """Fetch route-specific cached offers without ever exposing the API token."""

    def __init__(self, token: str | None = None) -> None:
        """Use an injected token for tests or the local environment token in production."""
        self._token = token if token is not None else os.getenv("TRAVELPAYOUTS_API_TOKEN")

    @property
    def configured(self) -> bool:
        """Report whether this environment can query the Flight Data API."""
        return bool(self._token)

    async def _find_legacy_connection_edges(
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
                        "transfer_count": 0,
                        "price_status": "cached_reference",
                        "price_currency": "EUR",
                        "price_expires_at": offer.get("expires_at"),
                        "duration_status": "TRO distance estimate",
                        **_flight_schedule_metadata(departure_at),
                    },
                )
            )
        return sorted(edges, key=lambda edge: edge.estimated_price_eur)

    async def _find_calendar_connection_edges(
        self,
        origin: TransportNode,
        destination: TransportNode,
        departure_date: str,
    ) -> list[TravelEdge]:
        """Read the legacy monthly cache when the date-specific cache is empty.

        Travelpayouts' date endpoint only retains recently observed fares. The
        calendar endpoint is still cached data, but can retain a price for the
        requested day even when the newer endpoint has no result. It is used as
        a fallback only and never turns an unpriced flight into a graph edge.
        """
        origin_iata = _airport_iata(origin)
        destination_iata = _airport_iata(destination)
        if not self.configured or not origin_iata or not destination_iata:
            return []
        try:
            date.fromisoformat(departure_date)
        except ValueError:
            return []
        try:
            async with httpx.AsyncClient(timeout=20) as client:
                response = await client.get(
                    TRAVELPAYOUTS_CALENDAR_PRICES_URL,
                    params={
                        "origin": origin_iata,
                        "destination": destination_iata,
                        "depart_date": departure_date,
                        "calendar_type": "departure_date",
                        "currency": "EUR",
                    },
                    headers={
                        "X-Access-Token": self._token,
                        "User-Agent": "TRO-Travel-Route-Optimizer/0.1",
                    },
                )
                response.raise_for_status()
                offer = response.json().get("data", {}).get(departure_date)
        except (httpx.HTTPError, ValueError) as error:
            raise FlightProviderUnavailableError(
                "Travelpayouts flight data is temporarily unavailable."
            ) from error
        if not isinstance(offer, dict) or not isinstance(offer.get("price"), (int, float)):
            return []
        distance_km = round(_great_circle_distance_km(origin, destination), 2)
        departure_at = offer.get("departure_at")
        return [TravelEdge(
            id=f"flight:travelpayouts-calendar:{origin_iata}:{destination_iata}:{departure_date}",
            origin_node_id=origin.id,
            destination_node_id=destination.id,
            mode="flight",
            edge_type="flight_connection",
            estimated_distance_km=distance_km,
            estimated_duration_minutes=_estimated_flight_duration_minutes(distance_km),
            estimated_price_eur=round(float(offer["price"]), 2),
            provider=TRAVELPAYOUTS_PROVIDER_NAME,
            metadata={
                "origin_iata": origin_iata,
                "destination_iata": destination_iata,
                "airline": offer.get("airline"),
                "flight_number": offer.get("flight_number"),
                "departure_at": departure_at,
                "transfers": _normalized_stop_count(offer.get("transfers")),
                "transfer_count": _normalized_stop_count(offer.get("transfers")) or 0,
                "price_status": "monthly_cached_reference",
                "price_currency": "EUR",
                "price_expires_at": offer.get("expires_at"),
                "duration_status": "TRO distance estimate",
                **_flight_schedule_metadata(departure_at),
            },
        )]

    async def find_connection_edges(
        self,
        origin: TransportNode,
        destination: TransportNode,
        departure_date: str,
    ) -> list[TravelEdge]:
        """Use Travelpayouts' current date-specific endpoint before the legacy cache.

        The v3 response includes itinerary duration, number of stops, and a
        search link when available. It is still reference data, not live booking
        inventory, but is materially richer than the old cheap-prices endpoint.
        """
        origin_iata = _airport_iata(origin)
        destination_iata = _airport_iata(destination)
        if not self.configured or not origin_iata or not destination_iata:
            return []
        try:
            date.fromisoformat(departure_date)
        except ValueError:
            return []
        try:
            async with httpx.AsyncClient(timeout=20) as client:
                response = await client.get(
                    TRAVELPAYOUTS_DATE_PRICES_URL,
                    params={
                        "origin": origin_iata,
                        "destination": destination_iata,
                        "departure_at": departure_date,
                        "one_way": "true",
                        "sorting": "price",
                        "direct": "false",
                        "currency": "eur",
                        "limit": 30,
                        "page": 1,
                        "token": self._token,
                    },
                    headers={"User-Agent": "TRO-Travel-Route-Optimizer/0.1"},
                )
                response.raise_for_status()
                offers = response.json().get("data", [])
        except (httpx.HTTPError, ValueError) as error:
            raise FlightProviderUnavailableError(
                "Travelpayouts flight data is temporarily unavailable."
            ) from error

        if not isinstance(offers, list):
            offers = []
        distance_km = round(_great_circle_distance_km(origin, destination), 2)
        edges: list[TravelEdge] = []
        for index, offer in enumerate(offers, start=1):
            if not isinstance(offer, dict) or not isinstance(offer.get("price"), (int, float)):
                continue
            duration_minutes, has_duration, duration_status, provider_duration = (
                _validated_itinerary_duration(
                    offer.get("duration"), offer.get("transfers"), distance_km
                )
            )
            ticket_link = offer.get("link")
            booking_url = None
            if isinstance(ticket_link, str) and ticket_link:
                booking_url = ticket_link if ticket_link.startswith("http") else f"https://www.aviasales.com/search/{ticket_link.lstrip('/')}"
            edges.append(TravelEdge(
                id=f"flight:travelpayouts-v3:{origin_iata}:{destination_iata}:{departure_date}:{index}",
                origin_node_id=origin.id,
                destination_node_id=destination.id,
                mode="flight",
                edge_type="flight_connection",
                estimated_distance_km=distance_km,
                estimated_duration_minutes=duration_minutes,
                estimated_price_eur=round(float(offer["price"]), 2),
                is_estimate=not has_duration,
                provider=TRAVELPAYOUTS_PROVIDER_NAME,
                booking_url=booking_url,
                metadata={
                    "origin_iata": offer.get("origin_airport") or origin_iata,
                    "destination_iata": offer.get("destination_airport") or destination_iata,
                    "airline": offer.get("airline"),
                    "flight_number": offer.get("flight_number"),
                    "departure_at": offer.get("departure_at"),
                    "transfers": _normalized_stop_count(offer.get("transfers")),
                    "transfer_count": _normalized_stop_count(offer.get("transfers")) or 0,
                    "price_status": "cached_reference_last_48_hours",
                    "price_currency": str(offer.get("currency") or "EUR").upper(),
                    "duration_status": duration_status,
                    "provider_duration_minutes": provider_duration,
                    **_flight_schedule_metadata(
                        offer.get("departure_at"),
                        duration_minutes if has_duration else None,
                    ),
                },
            ))
        if edges:
            return sorted(edges, key=lambda edge: edge.estimated_price_eur)
        calendar_edges = await self._find_calendar_connection_edges(
            origin, destination, departure_date
        )
        if calendar_edges:
            return calendar_edges
        return await self._find_legacy_connection_edges(origin, destination, departure_date)


async def discover_flight_edges(
    origin_nodes: list[TransportNode],
    destination_nodes: list[TransportNode],
    departure_date: str,
    travellers: int = 1,
) -> tuple[list[TravelEdge], list[dict[str, str]]]:
    """Query live detailed offers first, then cached references as a fallback."""
    detailed_provider = DuffelFlightProvider()
    cached_provider = TravelpayoutsFlightProvider()
    if not detailed_provider.configured and not cached_provider.configured:
        return [], [{
            "infrastructure_type": "flights",
            "message": "No flight provider is configured on this server.",
        }]
    try:
        requested_date = date.fromisoformat(departure_date)
    except ValueError:
        return [], [{
            "infrastructure_type": "flights",
            "message": "Flight search requires a valid departure date.",
        }]
    if requested_date < date.today():
        return [], [{
            "infrastructure_type": "flights",
            "message": "Flight reference prices cannot be requested for a past departure date. Choose a future date.",
        }]

    origin_airports = [node for node in origin_nodes if _airport_iata(node)]
    destination_airports = [node for node in destination_nodes if _airport_iata(node)]
    edges: list[TravelEdge] = []
    reference_cache = FlightReferenceRepository()
    warnings: list[dict[str, str]] = []
    missing_origin_codes = [node.name for node in origin_nodes if node.type == "airport" and not _airport_iata(node)]
    missing_destination_codes = [node.name for node in destination_nodes if node.type == "airport" and not _airport_iata(node)]
    if missing_origin_codes or missing_destination_codes:
        missing_airports = missing_origin_codes + missing_destination_codes
        warnings.append({
            "infrastructure_type": "flights",
            "message": "Flight search was skipped for airport nodes without an IATA code: " + ", ".join(missing_airports) + ".",
        })
    for origin_airport in origin_airports:
        for destination_airport in destination_airports:
            origin_iata = _airport_iata(origin_airport)
            destination_iata = _airport_iata(destination_airport)
            if not origin_iata or not destination_iata:
                continue
            if detailed_provider.configured:
                try:
                    detailed_edges = await detailed_provider.find_connection_edges(
                        origin_airport, destination_airport, departure_date, travellers
                    )
                    if detailed_edges:
                        # Live offers usually expire within minutes. They must never
                        # be written into the 24-hour Travelpayouts reference cache.
                        edges.extend(detailed_edges)
                        continue
                except FlightProviderUnavailableError:
                    warnings.append({
                        "infrastructure_type": "flights",
                        "message": f"Detailed live flight offers were unavailable for {origin_iata} → {destination_iata}; cached references were checked instead.",
                    })
            if cached_provider.configured:
                try:
                    provider_edges = await cached_provider.find_connection_edges(
                        origin_airport, destination_airport, departure_date
                    )
                    if provider_edges:
                        try:
                            reference_cache.save_edges(
                                origin_iata, destination_iata, departure_date, provider_edges
                            )
                        except SQLAlchemyError:
                            warnings.append({
                                "infrastructure_type": "flights",
                                "message": "A current flight reference was found, but it could not be saved in the local cache.",
                            })
                        edges.extend(provider_edges)
                        continue
                except FlightProviderUnavailableError:
                    pass
            try:
                cached_edges = reference_cache.get_edges(
                    origin_iata, destination_iata, departure_date,
                    origin_airport.id, destination_airport.id,
                )
            except SQLAlchemyError:
                cached_edges = []
            if cached_edges:
                edges.extend(_validate_restored_flight_edge(edge) for edge in cached_edges)
                warnings.append({
                    "infrastructure_type": "flights",
                    "message": f"Travelpayouts returned no current reference for {origin_iata} → {destination_iata}; a stored reference from the last 24 hours is being shown.",
                })
            else:
                warnings.append({
                    "infrastructure_type": "flights",
                    "message": f"No current or stored flight reference is available for {origin_iata} → {destination_iata} on this date.",
                })
    return edges, warnings
