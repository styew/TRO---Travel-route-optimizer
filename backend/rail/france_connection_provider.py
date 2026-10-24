"""Discover French domestic rail journeys from the isolated SNCF OTP graph."""

from __future__ import annotations

import os
import re
from datetime import date, datetime
from zoneinfo import ZoneInfo

import httpx

from backend.models.graph import TravelEdge
from backend.models.node import TransportNode
from backend.services.international_rail_provider import _itinerary_distance_km, _transfer_waits
from backend.services.rail_price_estimator import rail_price_estimator

OTP_FRANCE_URL = os.getenv("OTP_FRANCE_URL", "http://localhost:8083/otp/gtfs/v1")
PROVIDER_NAME = "OpenTripPlanner with SNCF France GTFS"


class FranceRailProviderUnavailableError(RuntimeError):
    """Raised when the optional isolated French rail planner is stopped."""


class FranceRailRoutingError(RuntimeError):
    """Raised when an OSM terminal cannot be resolved in the SNCF timetable."""


def _estimated_price(distance_km: float, booking_days: int, travellers: int) -> dict[str, object]:
    """Return a transparent French rail estimate until calibrated observations exist."""
    estimate = rail_price_estimator.estimate(
        market="FR", service_category="long_distance", distance_km=distance_km,
        booking_days_ahead=booking_days, passengers=travellers, provider_context="sncf",
    )
    if estimate is not None:
        return estimate.as_dict()
    typical = round(max(15.0, distance_km * 0.15) * travellers, 2)
    return {
        "price_min_eur": round(typical * 0.7, 2),
        "price_typical_eur": typical,
        "price_max_eur": round(typical * 1.35, 2),
        "price_confidence": "very_low",
        "price_source": "provisional French rail distance model; no calibrated profile",
    }


class OpenTripPlannerFranceRailProvider:
    """Resolve French terminals against the running French OTP station index."""

    def __init__(self, base_url: str | None = None) -> None:
        """Use an injected endpoint in tests or the France service in Docker."""
        self._base_url = (base_url or OTP_FRANCE_URL).rstrip("/")
        self._feed_id: str | None = None

    async def _query(self, query: str, variables: dict | None = None) -> dict:
        """Run one bounded GraphQL request against the private French planner."""
        try:
            async with httpx.AsyncClient(timeout=20) as client:
                response = await client.post(
                    self._base_url, json={"query": query, "variables": variables or {}}
                )
                response.raise_for_status()
                return response.json()
        except (httpx.HTTPError, ValueError) as error:
            raise FranceRailProviderUnavailableError(
                "The French rail timetable planner is unavailable."
            ) from error

    async def _station_location(self, node: TransportNode) -> dict:
        """Address the exact GTFS parent, including every operator's child stops."""
        station_id = node.metadata.get("gtfs_stop_ids", {}).get("fr-national-rail")
        if station_id:
            if self._feed_id is None:
                payload = await self._query("query { feeds { feedId } }")
                feeds = payload.get("data", {}).get("feeds", [])
                if payload.get("errors") or len(feeds) != 1 or not feeds[0].get("feedId"):
                    raise FranceRailProviderUnavailableError(
                        "The French planner did not expose one usable GTFS feed."
                    )
                self._feed_id = feeds[0]["feedId"]
            return {"stopLocation": {"stopLocationId": f"{self._feed_id}:{station_id}"}}
        if node.source == "sncf-gtfs":
            raise FranceRailRoutingError(f"Missing French GTFS parent ID for {node.name}.")
        safe_name = node.name.replace("\\", "\\\\").replace('"', '\\"')
        payload = await self._query(
            f'query {{ stops(name: "{safe_name}") {{ gtfsId name lat lon }} }}'
        )
        if payload.get("errors"):
            raise FranceRailProviderUnavailableError(
                "The French planner could not search its station index."
            )
        candidates: list[tuple[float, str]] = []
        requested = set(re.findall(r"[a-z0-9]+", node.name.casefold()))
        for stop in payload.get("data", {}).get("stops", []):
            stop_name = stop.get("name") or ""
            stop_id = stop.get("gtfsId")
            tokens = set(re.findall(r"[a-z0-9]+", stop_name.casefold()))
            if not stop_id or (requested and not (requested & tokens)):
                continue
            try:
                distance = (float(stop["lat"]) - node.latitude) ** 2 + (
                    float(stop["lon"]) - node.longitude
                ) ** 2
            except (KeyError, TypeError, ValueError):
                continue
            candidates.append((distance, stop_id))
        if not candidates:
            raise FranceRailRoutingError(
                f"No French timetable terminal matched {node.name}."
            )
        return {"stopLocation": {"stopLocationId": min(candidates)[1]}}

    async def find_edges(
        self, origin: TransportNode, destination: TransportNode, travel_date: str,
        departure: datetime, travellers: int, minimum_transfer_minutes: int,
        search_entire_day: bool,
    ) -> list[TravelEdge]:
        """Return scheduled domestic French rail connections with price estimates."""
        origin_location, destination_location = await self._station_location(origin), await self._station_location(destination)
        payload = await self._query(
            """
            query FranceRail($origin: PlanLabeledLocationInput!, $destination: PlanLabeledLocationInput!, $dateTime: PlanDateTimeInput!, $preferences: PlanPreferencesInput) {
              planConnection(origin: $origin, destination: $destination, dateTime: $dateTime, first: 20,
                searchWindow: \"PT24H\", preferences: $preferences,
                modes: { transitOnly: true, transit: { transit: [{ mode: RAIL }] } }) {
                edges { node { start end duration numberOfTransfers legs {
                  transitLeg distance from { name lat lon } to { name lat lon }
                  start { scheduledTime } end { scheduledTime }
                  route { shortName longName agency { name } }
                } } }
                routingErrors { code description }
              }
            }
            """,
            {
                "origin": {"label": origin.name, "location": origin_location},
                "destination": {"label": destination.name, "location": destination_location},
                "dateTime": {"earliestDeparture": departure.isoformat()},
                "preferences": ({"transit": {"transfer": {"slack": f"PT{minimum_transfer_minutes}M"}}}
                                if minimum_transfer_minutes else None),
            },
        )
        if payload.get("errors"):
            raise FranceRailProviderUnavailableError("The French planner returned a GraphQL error.")
        connection = payload.get("data", {}).get("planConnection", {})
        if connection.get("routingErrors"):
            raise FranceRailRoutingError("The French planner could not route the selected terminals.")
        booking_days = max(0, (date.fromisoformat(travel_date) - datetime.now(ZoneInfo("Europe/Paris")).date()).days)
        edges: list[TravelEdge] = []
        for index, result in enumerate(connection.get("edges", []), start=1):
            itinerary = result.get("node", {})
            legs = [leg for leg in itinerary.get("legs", []) if leg.get("transitLeg")]
            waits = _transfer_waits(legs)
            if not legs or any(wait.get("wait_minutes") is None or wait["wait_minutes"] < minimum_transfer_minutes for wait in waits):
                continue
            distance_km, distance_source = _itinerary_distance_km(legs)
            estimate = _estimated_price(distance_km, booking_days, travellers)
            start_key = re.sub(r"[^0-9]", "", str(itinerary.get("start") or departure.isoformat()))
            edges.append(TravelEdge(
                id=f"train:france:{origin.id}:{destination.id}:{travel_date}:{start_key}:{index}",
                origin_node_id=origin.id, destination_node_id=destination.id,
                mode="train", edge_type="train_connection", estimated_distance_km=distance_km,
                estimated_duration_minutes=max(1, round(float(itinerary.get("duration") or 0) / 60)),
                estimated_price_eur=float(estimate["price_typical_eur"]),
                is_estimate=True, provider=PROVIDER_NAME,
                metadata={
                    "ticket_coverage": "estimated French rail fare", **estimate,
                    "planned_departure": itinerary.get("start"), "planned_arrival": itinerary.get("end"),
                    "transfer_count": itinerary.get("numberOfTransfers", 0),
                    "minimum_transfer_minutes_required": minimum_transfer_minutes,
                    "transfer_waits": waits,
                    "total_transfer_wait_minutes": sum(wait["wait_minutes"] or 0 for wait in waits),
                    "distance_source": distance_source,
                    "legs": [{
                        "line": leg.get("route", {}).get("shortName"),
                        "route": leg.get("route", {}).get("longName"),
                        "operator": leg.get("route", {}).get("agency", {}).get("name"),
                        "from": leg.get("from", {}).get("name"), "to": leg.get("to", {}).get("name"),
                        "scheduled_departure": leg.get("start", {}).get("scheduledTime"),
                        "scheduled_arrival": leg.get("end", {}).get("scheduledTime"),
                    } for leg in legs],
                },
            ))
        return edges


async def discover_french_rail_edges(
    origin_nodes: list[TransportNode], destination_nodes: list[TransportNode], travel_date: str,
    departure_time: str, travellers: int, minimum_transfer_minutes: int, search_entire_day: bool,
) -> tuple[list[TravelEdge], list[dict[str, str]], dict[str, object]]:
    """Discover bounded France→France rail edges without involving other countries."""
    origins = [node for node in origin_nodes if node.type == "train_station"]
    destinations = [node for node in destination_nodes if node.type == "train_station"]
    if not origins or not destinations:
        return [], [], {"enabled": True, "reason": "French terminal nodes are required."}
    try:
        departure = datetime.fromisoformat(f"{travel_date}T{departure_time}:00").replace(tzinfo=ZoneInfo("Europe/Paris"))
    except ValueError:
        return [], [{"infrastructure_type": "french_rail", "message": "The selected departure time is invalid."}], {"enabled": True, "reason": "Invalid departure time."}
    if search_entire_day:
        departure = departure.replace(hour=0, minute=0)
    provider = OpenTripPlannerFranceRailProvider()
    edges: list[TravelEdge] = []
    errors = 0
    try:
        for origin in origins[:4]:
            for destination in destinations[:4]:
                try:
                    edges.extend(await provider.find_edges(origin, destination, travel_date, departure, travellers, minimum_transfer_minutes, search_entire_day))
                except FranceRailRoutingError:
                    errors += 1
    except FranceRailProviderUnavailableError:
        return [], [{"infrastructure_type": "french_rail", "message": "The French timetable planner is not running; start the France profile."}], {"enabled": True, "provider_available": False}
    unique_edges = list({edge.id: edge for edge in edges}.values())
    return unique_edges, [], {"enabled": True, "provider_available": True, "terminal_pairs_checked": len(origins[:4]) * len(destinations[:4]), "routing_errors": errors, "edge_count": len(unique_edges)}
