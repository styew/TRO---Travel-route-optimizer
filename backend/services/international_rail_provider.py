"""Discover paid international rail after a Deutschlandticket gateway leg.

This provider only queries the selected regional corridor graph.  It never
loads a European GTFS archive into the FastAPI process and never treats a
timetable as a live ticket offer.
"""

import os
import re
from datetime import date, datetime
from math import asin, cos, radians, sin, sqrt
from zoneinfo import ZoneInfo

import httpx

from backend.models.graph import TravelEdge
from backend.models.node import TransportNode
from backend.services.rail_network_catalog import (
    RailCorridor,
    corridor_destination_station_locations,
)
from backend.services.rail_price_estimator import rail_price_estimator


_LONG_DISTANCE_PLATFORM = re.compile(
    r"(?:OCE(?:ICE|TGV|INTERCITES|LYRIA)|(?:^|[-_:])(?:ICE|TGV|IC|EC|RJ|NJ)(?:[-_:]|$))",
    re.IGNORECASE,
)


class InternationalRailProviderUnavailableError(Exception):
    """Raised when the selected optional international graph is not running."""


class InternationalRailRoutingError(Exception):
    """Raised when no usable station can be resolved in the selected corridor."""


def _transfer_waits(legs: list[dict]) -> list[dict]:
    """Represent each scheduled train-to-train wait for frontend display."""
    waits: list[dict] = []
    for previous, following in zip(legs, legs[1:]):
        arrival = previous.get("end", {}).get("scheduledTime")
        departure = following.get("start", {}).get("scheduledTime")
        wait_minutes = None
        if arrival and departure:
            try:
                wait_minutes = max(0, round((datetime.fromisoformat(departure) - datetime.fromisoformat(arrival)).total_seconds() / 60))
            except ValueError:
                pass
        waits.append({
            "at_station": previous.get("to", {}).get("name"),
            "from_line": previous.get("route", {}).get("shortName"),
            "to_line": following.get("route", {}).get("shortName"),
            "scheduled_arrival": arrival,
            "scheduled_departure": departure,
            "wait_minutes": wait_minutes,
        })
    return waits


def _provider_context(legs: list[dict]) -> str:
    """Normalize operators only where the price profile distinguishes them."""
    agencies = " ".join(
        (leg.get("route", {}).get("agency", {}).get("name") or "").casefold()
        for leg in legs
    )
    if "öbb" in agencies or "oebb" in agencies:
        return "oebb"
    if "deutsche bahn" in agencies or "db fernverkehr" in agencies:
        return "db"
    return "any"


def _itinerary_distance_km(legs: list[dict]) -> tuple[float, str]:
    """Return reported rail distance or a transparent coordinate-based fallback.

    Some transit-only OTP graphs retain schedules but omit the geometry used
    to calculate ``leg.distance``. A missing geometry must not turn a valid
    international train into a fictitious zero-kilometre journey and make it
    ineligible for every fare profile. The fallback is deliberately marked as
    approximate and applies a modest rail-routing factor to great-circle legs.
    """
    reported_meters = sum(float(leg.get("distance") or 0) for leg in legs)
    if reported_meters > 0:
        return round(reported_meters / 1_000, 2), "provider_reported"

    kilometres = 0.0
    for leg in legs:
        origin, destination = leg.get("from", {}), leg.get("to", {})
        try:
            latitude_1, longitude_1 = radians(float(origin["lat"])), radians(float(origin["lon"]))
            latitude_2, longitude_2 = radians(float(destination["lat"])), radians(float(destination["lon"]))
        except (KeyError, TypeError, ValueError):
            continue
        latitude_delta, longitude_delta = latitude_2 - latitude_1, longitude_2 - longitude_1
        haversine = sin(latitude_delta / 2) ** 2 + cos(latitude_1) * cos(latitude_2) * sin(longitude_delta / 2) ** 2
        kilometres += 2 * 6_371.0088 * asin(sqrt(haversine))
    # Long-distance rail routes are usually longer than the straight-line
    # path. The factor is only for price-band selection, never a live quote.
    return round(kilometres * 1.15, 2), "coordinate_estimate" if kilometres else "unavailable"


def _provisional_price(distance_km: float, passengers: int) -> dict[str, object]:
    """Provide a clearly labelled fallback when no calibrated fare band exists.

    A timetable-valid connection must remain visible to the route planner even
    before TRO has collected a statistical price profile for its exact
    distance/operator/booking window.  This deliberately conservative model
    is a temporary ranking value, never a ticket offer: EUR 0.16 per rail-km
    with a EUR 35 floor, plus a wide uncertainty range.
    """
    typical = max(35.0, distance_km * 0.16) * max(1, passengers)
    return {
        "minimum_eur": round(typical * 0.7, 2),
        "typical_eur": round(typical, 2),
        "maximum_eur": round(typical * 1.35, 2),
        "confidence": "very_low",
        "sample_size": None,
        "price_status": "provisional_distance_estimate",
        "profile_key": None,
        "source": "TRO provisional international rail distance model",
        "disclaimer": "No calibrated fare profile matched this scheduled route. This provisional estimate is not a live fare or availability confirmation.",
    }


class OpenTripPlannerInternationalRailProvider:
    """Query one selected OTP corridor and price only its paid ticket segment."""

    def __init__(self, corridor: RailCorridor) -> None:
        """Read the URL lazily so optional Docker profiles remain optional."""
        self._corridor = corridor
        self._base_url = os.getenv(corridor.graph_url_environment, "http://localhost:8082/otp/gtfs/v1").rstrip("/")

    async def _query(self, query: str, variables: dict | None = None) -> dict:
        """Call the isolated GraphQL endpoint and hide transport details from routes."""
        try:
            async with httpx.AsyncClient(timeout=35) as client:
                response = await client.post(self._base_url, json={"query": query, "variables": variables or {}})
                response.raise_for_status()
                return response.json()
        except (httpx.HTTPError, ValueError) as error:
            raise InternationalRailProviderUnavailableError("The selected international rail graph is unavailable.") from error

    async def _graph_stop_location(
        self, node: TransportNode, lookup_name: str | None = None,
        stop_location_id: str | None = None,
    ) -> dict:
        """Resolve a city or gateway using the running graph's own station index.

        A filtered GTFS archive can keep a different platform subset from the
        final OTP graph. Deriving a ``feed:stop_id`` from the zip can therefore
        look plausible while still failing in the planner. ``stops(name:)``
        returns the exact live ID, including its feed and platform namespace.
        We then choose the matching result geographically closest to the TRO
        node. This also works when a city has no retained parent station.
        """
        # GraphQL string literals need only the two escaping operations below;
        # station names originate from our own geocoding/catalog data.
        if stop_location_id:
            return {"stopLocation": {"stopLocationId": stop_location_id}}
        station_name = lookup_name or node.name
        safe_name = station_name.replace("\\", "\\\\").replace('"', '\\"')
        payload = await self._query(
            f'query {{ stops(name: "{safe_name}") {{ gtfsId name lat lon }} }}'
        )
        live_stops = payload.get("data", {}).get("stops", [])
        if payload.get("errors"):
            raise InternationalRailProviderUnavailableError(
                "The international rail graph could not search its station index."
            )
        candidates: list[tuple[int, float, str]] = []
        requested_tokens = set(re.findall(r"[a-z0-9]+", station_name.casefold()))
        for stop in live_stops:
            stop_id = stop.get("gtfsId")
            stop_name = stop.get("name") or ""
            if not stop_id:
                continue
            # Avoid a distant city with a coincidentally similar stop name.
            stop_tokens = set(re.findall(r"[a-z0-9]+", stop_name.casefold()))
            if requested_tokens and not (requested_tokens & stop_tokens):
                continue
            try:
                distance_score = (float(stop["lat"]) - node.latitude) ** 2 + (
                    float(stop["lon"]) - node.longitude
                ) ** 2
            except (KeyError, TypeError, ValueError):
                continue
            # In an international timetable a terminal often has isolated
            # platform records. Prefer platforms used by long-distance trains
            # over a geographically identical local/TER platform. OTP has no
            # walking graph in this lightweight transit-only deployment, so
            # selecting the wrong platform would make a real direct service
            # look disconnected.
            long_distance_platform = bool(_LONG_DISTANCE_PLATFORM.search(stop_id))
            candidates.append((0 if long_distance_platform else 1, distance_score, stop_id))
        if candidates:
            return {"stopLocation": {"stopLocationId": min(candidates)[2]}}
        raise InternationalRailRoutingError(
            f"No scheduled international station was found for {station_name} in corridor {self._corridor.key}."
        )

    async def find_paid_edges(
        self, origin: TransportNode, destination: TransportNode, travel_date: str,
        departure_at: datetime, passengers: int, minimum_transfer_minutes: int,
        search_entire_day: bool, destination_terminal: dict[str, str] | None = None,
    ) -> tuple[list[TravelEdge], dict[str, object]]:
        """Return paid timetable edges and an auditable count of every filter."""
        origin_location = await self._graph_stop_location(origin)
        terminal_name = (destination_terminal or {}).get("name")
        destination_location = await self._graph_stop_location(
            destination,
            lookup_name=terminal_name,
            stop_location_id=(destination_terminal or {}).get("stop_location_id"),
        )
        query = """
        query InternationalRail($origin: PlanLabeledLocationInput!, $destination: PlanLabeledLocationInput!, $dateTime: PlanDateTimeInput!, $preferences: PlanPreferencesInput) {
          planConnection(origin: $origin, destination: $destination, dateTime: $dateTime, first: 10,
            searchWindow: \"PT24H\", preferences: $preferences,
            modes: { transitOnly: true, transit: { transit: [{ mode: RAIL }] } }) {
            edges { node { start end duration numberOfTransfers legs {
              mode transitLeg distance from { name lat lon } to { name lat lon }
              start { scheduledTime } end { scheduledTime }
              route { shortName longName agency { name } }
            } } }
            routingErrors { code description }
          }
        }
        """
        payload = await self._query(query, {
            "origin": {"label": origin.name, "location": origin_location},
            "destination": {"label": destination.name, "location": destination_location},
            "dateTime": {"earliestDeparture": departure_at.isoformat()},
            "preferences": ({"transit": {"transfer": {"slack": f"PT{minimum_transfer_minutes}M"}}}
                            if minimum_transfer_minutes else None),
        })
        connection = payload.get("data", {}).get("planConnection", {})
        if payload.get("errors"):
            raise InternationalRailProviderUnavailableError("The international rail graph returned a GraphQL error.")
        diagnostic: dict[str, object] = {
            "gateway": origin.name,
            "destination_terminal": terminal_name or destination.name,
            "resolved_origin_stop": origin_location["stopLocation"]["stopLocationId"],
            "resolved_destination_stop": destination_location["stopLocation"]["stopLocationId"],
            "planner_itineraries": len(connection.get("edges", [])),
            "discarded_without_transit_legs": 0,
            "discarded_transfer_time": 0,
            "used_price_profile": 0,
            "used_provisional_price": 0,
        }
        if connection.get("routingErrors"):
            diagnostic["routing_errors"] = connection["routingErrors"]
            return [], diagnostic
        try:
            booking_days = max(0, (date.fromisoformat(travel_date) - datetime.now(ZoneInfo("Europe/Berlin")).date()).days)
        except ValueError:
            diagnostic["invalid_travel_date"] = True
            return [], diagnostic
        edges: list[TravelEdge] = []
        for index, result in enumerate(connection.get("edges", []), start=1):
            itinerary = result.get("node", {})
            legs = [leg for leg in itinerary.get("legs", []) if leg.get("transitLeg")]
            if not legs:
                diagnostic["discarded_without_transit_legs"] += 1
                continue
            waits = _transfer_waits(legs)
            if any(wait["wait_minutes"] is None or wait["wait_minutes"] < minimum_transfer_minutes for wait in waits):
                diagnostic["discarded_transfer_time"] += 1
                continue
            distance_km, distance_source = _itinerary_distance_km(legs)
            estimate = rail_price_estimator.estimate(
                market=f"{self._corridor.origin_country}-{self._corridor.destination_country}",
                service_category="international", distance_km=distance_km,
                booking_days_ahead=booking_days, passengers=passengers,
                provider_context=_provider_context(legs),
            )
            # Do not erase a valid timetable because our price research is
            # incomplete. Use a clearly marked, conservative fallback instead.
            if estimate is None:
                estimate_data = _provisional_price(distance_km, passengers)
                diagnostic["used_provisional_price"] += 1
            else:
                estimate_data = estimate.as_dict()
                diagnostic["used_price_profile"] += 1
            route_start = re.sub(r"[^0-9]", "", str(itinerary.get("start") or departure_at.isoformat()))
            edges.append(TravelEdge(
                id=f"train:international:{self._corridor.key}:{origin.id}:{destination.id}:{travel_date}:{route_start}:{index}",
                origin_node_id=origin.id,
                destination_node_id=destination.id,
                mode="train", edge_type="train_connection",
                estimated_distance_km=distance_km,
                estimated_duration_minutes=max(1, round(float(itinerary.get("duration") or 0) / 60)),
                estimated_price_eur=float(estimate_data["typical_eur"]),
                is_estimate=True,
                provider=f"OpenTripPlanner corridor {self._corridor.key}",
                metadata={
                    "ticket_coverage": "estimated paid international rail fare",
                    "ticket_boundary": "Deutschlandticket_to_paid_international_rail",
                    "required_preceding_transfer_minutes": origin.metadata.get("minimum_international_transfer_minutes", 15),
                    "corridor": self._corridor.key,
                    **estimate_data,
                    "planned_departure": itinerary.get("start"),
                    "planned_arrival": itinerary.get("end"),
                    "transfer_count": itinerary.get("numberOfTransfers", 0),
                    "minimum_transfer_minutes_required": minimum_transfer_minutes,
                    "distance_source": distance_source,
                    "transfer_waits": waits,
                    "total_transfer_wait_minutes": sum(wait["wait_minutes"] or 0 for wait in waits),
                    "legs": [{
                        "line": leg.get("route", {}).get("shortName"),
                        "route": leg.get("route", {}).get("longName"),
                        "operator": leg.get("route", {}).get("agency", {}).get("name"),
                        "from": leg.get("from", {}).get("name"),
                        "to": leg.get("to", {}).get("name"),
                        "scheduled_departure": leg.get("start", {}).get("scheduledTime"),
                        "scheduled_arrival": leg.get("end", {}).get("scheduledTime"),
                    } for leg in legs],
                },
            ))
        diagnostic["created_edges"] = len(edges)
        return edges, diagnostic


async def discover_international_rail_edges(
    corridor: RailCorridor | None, gateway_nodes: list[TransportNode], destination_city: TransportNode,
    travel_date: str, departure_time: str, travellers: int, minimum_transfer_minutes: int,
    search_entire_day: bool,
) -> tuple[list[TravelEdge], list[dict[str, str]], dict[str, object]]:
    """Discover paid rail from bounded D-Ticket gateways to an overseas city anchor."""
    if corridor is None or not gateway_nodes:
        return [], [], {"enabled": False, "reason": "No configured corridor or gateway stations."}
    try:
        departure = datetime.fromisoformat(f"{travel_date}T{departure_time}:00").replace(tzinfo=ZoneInfo("Europe/Berlin"))
    except ValueError:
        return [], [{"infrastructure_type": "international_rail", "message": "The selected departure time is invalid."}], {"enabled": True, "reason": "Invalid departure time."}
    if search_entire_day:
        departure = departure.replace(hour=0, minute=0)
    provider = OpenTripPlannerInternationalRailProvider(corridor)
    destination_terminals = corridor_destination_station_locations(corridor, destination_city)
    # Preserve a city-centre lookup for a feed where terminal names do not
    # include the city name, but use GTFS terminals whenever they are known.
    if not destination_terminals:
        destination_terminals = [{"name": destination_city.name}]
    edges: list[TravelEdge] = []
    unresolved_gateways: list[InternationalRailRoutingError] = []
    gateway_reports: list[dict[str, object]] = []
    try:
        # Try one real destination terminal across every candidate gateway
        # before falling back to the next terminal. This avoids multiplying a
        # normal request into every gateway × every Paris station and lets a
        # direct Mannheim → Paris Est service end the discovery early.
        for terminal in destination_terminals:
            for gateway in gateway_nodes:
                try:
                    found_edges, report = await provider.find_paid_edges(
                        gateway, destination_city, travel_date, departure, travellers,
                        minimum_transfer_minutes, search_entire_day, terminal,
                    )
                    edges.extend(found_edges)
                    gateway_reports.append(report)
                except InternationalRailRoutingError as error:
                    # A GTFS-derived candidate can be retained in the regional
                    # graph but absent from this corridor's final platform index.
                    # That station should not prevent later candidates from being
                    # checked on the same request.
                    unresolved_gateways.append(error)
                    gateway_reports.append({
                        "gateway": gateway.name,
                        "destination_terminal": terminal["name"],
                        "stage": "station_resolution",
                        "error": str(error),
                    })
                    continue
            if edges:
                break
    except InternationalRailProviderUnavailableError:
        return [], [{
            "infrastructure_type": "international_rail",
            "message": "The selected international rail graph is not running; start the Europe profile to search this corridor.",
        }], {"enabled": True, "corridor": corridor.key, "destination_terminals": [terminal["name"] for terminal in destination_terminals], "gateway_reports": gateway_reports, "provider_available": False}
    unique_edges = list({edge.id: edge for edge in edges}.values())
    if not unique_edges:
        station_note = (
            " Some timetable-derived stations could not be resolved in the running corridor."
            if unresolved_gateways else ""
        )
        return [], [{
            "infrastructure_type": "international_rail",
            "message": (
                "No priced international rail continuation was scheduled from the "
                "available Germany gateway stations on the selected date." + station_note
            ),
        }], {"enabled": True, "corridor": corridor.key, "destination_terminals": [terminal["name"] for terminal in destination_terminals], "provider_available": True, "gateway_reports": gateway_reports, "created_edge_count": 0}
    return unique_edges, [], {"enabled": True, "corridor": corridor.key, "destination_terminals": [terminal["name"] for terminal in destination_terminals], "provider_available": True, "gateway_reports": gateway_reports, "created_edge_count": len(unique_edges)}
