"""Discover reusable published flight schedules through AeroDataBox.

This module intentionally deals with schedules only. AeroDataBox does not make
an itinerary bookable and it does not provide a reliable ticket price. Prices
are therefore joined later from TRO's historical observations or a clearly
labelled local estimate.
"""

from __future__ import annotations

import math
import os
from datetime import date, datetime
from typing import Any

import httpx
from dotenv import load_dotenv
from sqlalchemy.exc import SQLAlchemyError

from backend.database.repository import (
    FlightPriceObservationRepository,
    ScheduledFlightRepository,
)
from backend.models.graph import TravelEdge
from backend.models.node import TransportNode

load_dotenv()

AERODATABOX_HOST = "aerodatabox.p.rapidapi.com"
AERODATABOX_FIDS_URL = f"https://{AERODATABOX_HOST}/flights/airports/iata"
AERODATABOX_SOURCE = "AeroDataBox schedule via RapidAPI"


class FlightScheduleProviderUnavailableError(RuntimeError):
    """Raised when an authenticated schedule refresh cannot be completed."""


def _iata_code(node: TransportNode) -> str | None:
    """Return a normalized IATA code only from an airport graph node."""
    code = node.metadata.get("iata") if node.type == "airport" else None
    return code.upper() if isinstance(code, str) and len(code) == 3 else None


def _timestamp(value: object) -> str | None:
    """Normalize AeroDataBox local or UTC time values to ISO-8601 text."""
    if isinstance(value, dict):
        value = value.get("local") or value.get("utc")
    if not isinstance(value, str) or not value.strip():
        return None
    candidate = value.strip().replace("Z", "+00:00")
    if " " in candidate and "T" not in candidate:
        candidate = candidate.replace(" ", "T", 1)
    try:
        return datetime.fromisoformat(candidate).isoformat()
    except ValueError:
        return None


def _movement(payload: object) -> dict[str, Any]:
    """Return a provider movement object without assuming an undocumented shape."""
    return payload if isinstance(payload, dict) else {}


def _airport_code(movement: dict[str, Any]) -> str | None:
    """Extract an IATA identifier from one arrival or departure movement."""
    airport = movement.get("airport")
    if not isinstance(airport, dict):
        return None
    code = airport.get("iata")
    return code.upper() if isinstance(code, str) and len(code) == 3 else None


def _airport_name(movement: dict[str, Any], fallback: str) -> str:
    """Return a readable airport label while retaining its IATA fallback."""
    airport = movement.get("airport")
    name = airport.get("name") if isinstance(airport, dict) else None
    return name if isinstance(name, str) and name else fallback


def _scheduled_time(movement: dict[str, Any]) -> str | None:
    """Prefer published scheduled time over revised operational time."""
    return _timestamp(movement.get("scheduledTime")) or _timestamp(movement.get("revisedTime"))


def _provider_rows(payload: object) -> list[dict[str, Any]]:
    """Support the documented FIDS object and a list response defensively."""
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    if not isinstance(payload, dict):
        return []
    departures = payload.get("departures")
    if isinstance(departures, list):
        return [item for item in departures if isinstance(item, dict)]
    return []


class AeroDataBoxScheduleProvider:
    """Read scheduled departures for one airport in two permitted 12-hour pages."""

    def __init__(self, token: str | None = None) -> None:
        """Use an injected token in tests or the private local environment value."""
        self._token = token if token is not None else os.getenv("AERODATABOX_RAPIDAPI_KEY")

    @property
    def configured(self) -> bool:
        """Report whether the backend can request AeroDataBox without exposing a token."""
        return bool(self._token)

    async def departures_for_date(
        self, origin_iata: str, service_date: str, destination_iatas: set[str],
    ) -> list[dict[str, Any]]:
        """Return direct scheduled departures matching selected destination airports.

        AeroDataBox FIDS requests are limited to a 12-hour local range. Two
        requests cover the requested day and no request is sent for unrelated
        destination airports.
        """
        if not self.configured:
            return []
        try:
            requested_date = date.fromisoformat(service_date)
        except ValueError as error:
            raise FlightScheduleProviderUnavailableError("Invalid flight schedule date.") from error
        ranges = (
            (f"{requested_date.isoformat()}T00:00", f"{requested_date.isoformat()}T12:00"),
            (f"{requested_date.isoformat()}T12:00", f"{requested_date.isoformat()}T23:59"),
        )
        headers = {
            "X-RapidAPI-Key": self._token,
            "X-RapidAPI-Host": AERODATABOX_HOST,
            "User-Agent": "TRO-Travel-Route-Optimizer/0.1",
        }
        flights: list[dict[str, Any]] = []
        try:
            async with httpx.AsyncClient(timeout=20.0) as client:
                for from_local, to_local in ranges:
                    response = await client.get(
                        f"{AERODATABOX_FIDS_URL}/{origin_iata}/{from_local}/{to_local}",
                        params={
                            "direction": "Departure",
                            "withLeg": "true",
                            "withCancelled": "false",
                            "withCodeshared": "false",
                            "withCargo": "false",
                            "withPrivate": "false",
                        },
                        headers=headers,
                    )
                    response.raise_for_status()
                    flights.extend(self._normalize_departures(
                        _provider_rows(response.json()), origin_iata, service_date,
                        destination_iatas,
                    ))
        except (httpx.HTTPError, ValueError) as error:
            raise FlightScheduleProviderUnavailableError(
                "AeroDataBox schedules are temporarily unavailable."
            ) from error
        return flights

    @staticmethod
    def _normalize_departures(
        rows: list[dict[str, Any]], origin_iata: str, service_date: str,
        destination_iatas: set[str],
    ) -> list[dict[str, Any]]:
        """Normalize only complete direct passenger schedule rows for storage."""
        flights: list[dict[str, Any]] = []
        seen: set[tuple[str, str, str]] = set()
        for row in rows:
            departure = _movement(row.get("departure") or row.get("movement"))
            arrival = _movement(row.get("arrival"))
            destination_iata = _airport_code(arrival)
            planned_departure = _scheduled_time(departure)
            planned_arrival = _scheduled_time(arrival)
            number = row.get("number") or row.get("flightNumber")
            if (
                destination_iata not in destination_iatas
                or not planned_departure
                or not planned_arrival
                or not isinstance(number, str)
                or not number.strip()
            ):
                continue
            airline = row.get("airline") if isinstance(row.get("airline"), dict) else {}
            key = (destination_iata, planned_departure, number.strip().upper())
            if key in seen:
                continue
            seen.add(key)
            flights.append({
                "origin_iata": origin_iata,
                "destination_iata": destination_iata,
                "service_date": service_date,
                "planned_departure": planned_departure,
                "planned_arrival": planned_arrival,
                "flight_number": number.strip().upper(),
                "airline_code": airline.get("iata") if isinstance(airline.get("iata"), str) else None,
                "airline_name": airline.get("name") if isinstance(airline.get("name"), str) else None,
                "source": AERODATABOX_SOURCE,
                "source_url": None,
                "source_updated_at": None,
            })
        return flights


def _distance_km(origin: TransportNode, destination: TransportNode) -> float:
    """Calculate a great-circle distance used only for an explicit price fallback."""
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


def _price_for_schedule(
    origin: TransportNode, destination: TransportNode, departure_date: str,
) -> tuple[float, dict[str, Any]]:
    """Attach a historical price range or a transparent distance-based fallback."""
    origin_iata = _iata_code(origin) or ""
    destination_iata = _iata_code(destination) or ""
    estimate = FlightPriceObservationRepository().get_estimate(
        origin_iata, destination_iata, departure_date
    )
    if estimate:
        return float(estimate["typical_eur"]), {
            "price_status": "historic_estimate",
            "price_estimate": estimate,
        }
    distance = _distance_km(origin, destination)
    price = round(max(39.0, 28.0 + distance * 0.14), 2)
    return price, {
        "price_status": "model_estimate",
        "price_estimate": {
            "kind": "distance_model_estimate",
            "currency": "EUR",
            "typical_eur": price,
            "confidence": "low",
            "disclaimer": "A published schedule was found, but no historic fare observation exists for this airport pair. This is a non-bookable route-length estimate.",
        },
    }


def _edge_from_schedule(
    schedule: dict[str, Any], origin: TransportNode, destination: TransportNode,
    departure_date: str, index: int,
) -> TravelEdge | None:
    """Convert one stored direct schedule into an informative flight graph edge."""
    departure = _timestamp(schedule.get("planned_departure"))
    arrival = _timestamp(schedule.get("planned_arrival"))
    if not departure or not arrival:
        return None
    try:
        duration_minutes = max(1, round((
            datetime.fromisoformat(arrival) - datetime.fromisoformat(departure)
        ).total_seconds() / 60))
    except ValueError:
        return None
    price, price_metadata = _price_for_schedule(origin, destination, departure_date)
    origin_iata = _iata_code(origin) or schedule["origin_iata"]
    destination_iata = _iata_code(destination) or schedule["destination_iata"]
    airline = schedule.get("airline_name") or schedule.get("airline_code") or "Airline unavailable"
    return TravelEdge(
        id=f"flight:scheduled:{origin_iata}:{destination_iata}:{departure_date}:{index}",
        origin_node_id=origin.id,
        destination_node_id=destination.id,
        mode="flight",
        edge_type="flight_connection",
        estimated_distance_km=round(_distance_km(origin, destination), 2),
        estimated_duration_minutes=duration_minutes,
        estimated_price_eur=price,
        is_estimate=True,
        provider="AeroDataBox published schedule; TRO price estimate",
        metadata={
            "origin_iata": origin_iata,
            "destination_iata": destination_iata,
            "flight_number": schedule["flight_number"],
            "airline": airline,
            "airline_code": schedule.get("airline_code"),
            "transfer_count": 0,
            "duration_status": "AeroDataBox published schedule",
            "planned_departure": departure,
            "planned_arrival": arrival,
            "schedule_source": schedule["source"],
            "schedule_imported_at": schedule.get("imported_at"),
            "schedule_source_updated_at": schedule.get("source_updated_at"),
            "flight_legs": [{
                "from": origin.name,
                "from_iata": origin_iata,
                "to": destination.name,
                "to_iata": destination_iata,
                "scheduled_departure": departure,
                "scheduled_arrival": arrival,
                "duration_minutes": duration_minutes,
                "airline": airline,
                "airline_code": schedule.get("airline_code"),
                "flight_number": schedule["flight_number"],
            }],
            "transfer_waits": [],
            **price_metadata,
        },
    )


async def discover_scheduled_flight_edges(
    origin_nodes: list[TransportNode], destination_nodes: list[TransportNode],
    departure_date: str,
) -> tuple[list[TravelEdge], list[dict[str, str]]]:
    """Load cached schedules first and refresh only missing origin-airport days."""
    provider = AeroDataBoxScheduleProvider()
    repository = ScheduledFlightRepository()
    try:
        date.fromisoformat(departure_date)
    except ValueError:
        return [], [{"infrastructure_type": "flight_schedules", "message": "Flight schedules require a valid departure date."}]
    origin_airports = [node for node in origin_nodes if _iata_code(node)]
    destination_airports = [node for node in destination_nodes if _iata_code(node)]
    destination_by_iata = {_iata_code(node): node for node in destination_airports}
    destination_iatas = set(destination_by_iata)
    if not origin_airports or not destination_iatas:
        return [], []
    warnings: list[dict[str, str]] = []
    for origin in origin_airports:
        origin_iata = _iata_code(origin)
        if not origin_iata or not repository.refresh_is_due(origin_iata, departure_date, AERODATABOX_SOURCE):
            continue
        if not provider.configured:
            warnings.append({
                "infrastructure_type": "flight_schedules",
                "message": "AeroDataBox is not configured; only stored schedules can be used.",
            })
            continue
        try:
            schedules = await provider.departures_for_date(
                origin_iata, departure_date, destination_iatas
            )
            repository.upsert_many(schedules)
            repository.mark_refreshed(origin_iata, departure_date, AERODATABOX_SOURCE)
        except (FlightScheduleProviderUnavailableError, SQLAlchemyError) as error:
            warnings.append({
                "infrastructure_type": "flight_schedules",
                "message": f"Published schedules for {origin_iata} could not be refreshed; stored schedules were checked instead.",
            })

    edges: list[TravelEdge] = []
    for origin in origin_airports:
        origin_iata = _iata_code(origin)
        if not origin_iata:
            continue
        for destination_iata, destination in destination_by_iata.items():
            schedules = repository.get_direct_flights(
                origin_iata, destination_iata, departure_date, AERODATABOX_SOURCE
            )
            for index, schedule in enumerate(schedules, start=1):
                edge = _edge_from_schedule(schedule, origin, destination, departure_date, index)
                if edge:
                    edges.append(edge)
    return edges, warnings
