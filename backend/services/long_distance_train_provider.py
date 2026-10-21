"""Create scheduled German long-distance rail edges with transparent fare estimates.

The official GTFS.DE long-distance feed supplies the timetable.  It does not
publish ticket offers, so TRO deliberately applies its own statistical price
profiles and labels every resulting fare as an estimate.
"""

import os
import re
from datetime import date, datetime
from zoneinfo import ZoneInfo

import httpx

from backend.models.graph import TravelEdge
from backend.models.node import TransportNode
from backend.services.rail_price_estimator import rail_price_estimator
from backend.services.regional_gtfs import find_gtfs_station_id

OTP_LONG_DISTANCE_URL = os.getenv("OTP_LONG_DISTANCE_URL", "http://localhost:8081/otp/gtfs/v1")
LONG_DISTANCE_GTFS_ARCHIVE_PATH = os.getenv(
    "LONG_DISTANCE_GTFS_ARCHIVE_PATH", "/app/otp-data/long-distance/germany-long-distance-gtfs.zip"
)
PROVIDER_NAME = "OpenTripPlanner with official GTFS.DE long-distance rail"


class LongDistanceTrainProviderUnavailableError(Exception):
    """Raised when the local long-distance OpenTripPlanner graph is unavailable."""


class LongDistanceTrainRoutingError(Exception):
    """Raised when the local graph cannot resolve a requested station pair."""


def _transfer_waits(legs: list[dict]) -> list[dict]:
    """Extract every scheduled station change and its waiting time from one journey."""
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
    """Classify the main operator only when a calibration profile supports it."""
    agencies = " ".join(
        (leg.get("route", {}).get("agency", {}).get("name") or "").casefold()
        for leg in legs
    )
    if "öbb" in agencies or "oebb" in agencies:
        return "oebb"
    if "deutsche bahn" in agencies or "db fernverkehr" in agencies:
        return "db"
    return "any"


class OpenTripPlannerLongDistanceTrainProvider:
    """Query the long-distance graph while keeping OpenTripPlanner private to the backend."""

    def __init__(self, base_url: str | None = None) -> None:
        """Use the configured long-distance planner or an injected test endpoint."""
        self._base_url = (base_url or OTP_LONG_DISTANCE_URL).rstrip("/")

    async def _query(self, query: str, variables: dict | None = None) -> dict:
        """Run one GraphQL query and turn connection failures into a domain error."""
        try:
            async with httpx.AsyncClient(timeout=30) as client:
                response = await client.post(self._base_url, json={"query": query, "variables": variables or {}})
                response.raise_for_status()
                return response.json()
        except (httpx.HTTPError, ValueError) as error:
            raise LongDistanceTrainProviderUnavailableError(
                "The local long-distance train planner is unavailable."
            ) from error

    async def _planner_stop_location(self, node: TransportNode) -> dict:
        """Map an infrastructure node to the selected graph's GTFS parent station."""
        feed_payload = await self._query("query { feeds { feedId } }")
        feeds = feed_payload.get("data", {}).get("feeds", [])
        if feed_payload.get("errors") or len(feeds) != 1:
            raise LongDistanceTrainProviderUnavailableError(
                "The long-distance planner did not expose one usable GTFS feed."
            )
        station_id = find_gtfs_station_id(
            node.name, node.latitude, node.longitude, LONG_DISTANCE_GTFS_ARCHIVE_PATH
        )
        if station_id is None:
            raise LongDistanceTrainRoutingError(
                f"No matching long-distance GTFS station was found for {node.name}."
            )
        return {"stopLocation": {"stopLocationId": f"{feeds[0]['feedId']}:{station_id}"}}

    async def find_estimated_fare_edges(
        self, origin: TransportNode, destination: TransportNode, travel_date: str,
        departure_at: datetime, minimum_transfer_minutes: int, passengers: int,
        search_entire_day: bool = False,
    ) -> list[TravelEdge]:
        """Return timetable-valid long-distance journeys which have an estimate profile."""
        if origin.type != "train_station" or destination.type != "train_station":
            return []
        origin_location = await self._planner_stop_location(origin)
        destination_location = await self._planner_stop_location(destination)
        search_window = "PT24H" if search_entire_day else "PT12H"
        query = """
        query LongDistanceRail($origin: PlanLabeledLocationInput!, $destination: PlanLabeledLocationInput!, $dateTime: PlanDateTimeInput!, $preferences: PlanPreferencesInput) {
          planConnection(origin: $origin, destination: $destination, dateTime: $dateTime, first: 30,
            searchWindow: "$SEARCH_WINDOW", preferences: $preferences,
            modes: { transitOnly: true, transit: { transit: [{ mode: RAIL }] } }) {
            edges { node { start end duration numberOfTransfers legs {
              mode transitLeg distance from { name lat lon } to { name lat lon }
              start { scheduledTime } end { scheduledTime }
              route { shortName longName agency { name } }
            } } }
            routingErrors { code description }
          }
        }
        """.replace("$SEARCH_WINDOW", search_window)
        variables = {
            "origin": {"label": origin.name, "location": origin_location},
            "destination": {"label": destination.name, "location": destination_location},
            "dateTime": {"earliestDeparture": departure_at.isoformat()},
            "preferences": ({"transit": {"transfer": {"slack": f"PT{minimum_transfer_minutes}M"}}}
                            if minimum_transfer_minutes else None),
        }
        payload = await self._query(query, variables)
        connection = payload.get("data", {}).get("planConnection", {})
        if payload.get("errors"):
            raise LongDistanceTrainProviderUnavailableError("The long-distance planner returned a GraphQL error.")
        if connection.get("routingErrors"):
            error = connection["routingErrors"][0]
            raise LongDistanceTrainRoutingError(
                f"The long-distance planner could not route the selected stations (OTP: {error.get('code', 'UNKNOWN')}; {error.get('description') or 'no details'})."
            )
        try:
            booking_days = max(0, (date.fromisoformat(travel_date) - datetime.now(ZoneInfo("Europe/Berlin")).date()).days)
        except ValueError:
            return []
        edges: list[TravelEdge] = []
        for index, result in enumerate(connection.get("edges", []), start=1):
            itinerary = result.get("node", {})
            legs = [leg for leg in itinerary.get("legs", []) if leg.get("transitLeg")]
            if not legs:
                continue
            waits = _transfer_waits(legs)
            if any(wait.get("wait_minutes") is None or wait["wait_minutes"] < minimum_transfer_minutes for wait in waits):
                continue
            distance_km = round(sum(float(leg.get("distance") or 0) for leg in legs) / 1_000, 2)
            estimate = rail_price_estimator.estimate(
                market="DE", service_category="long_distance", distance_km=distance_km,
                booking_days_ahead=booking_days, passengers=passengers,
                provider_context=_provider_context(legs),
            )
            # A timetable alone must never become a fake EUR 0 offer.  More
            # profiles can be added as the user calibrates additional corridors.
            if estimate is None:
                continue
            route_start = re.sub(r"[^0-9]", "", str(itinerary.get("start") or departure_at.isoformat()))
            edges.append(TravelEdge(
                id=f"train:long-distance:{origin.id}:{destination.id}:{travel_date}:{route_start}:{index}",
                origin_node_id=origin.id, destination_node_id=destination.id,
                mode="train", edge_type="train_connection", estimated_distance_km=distance_km,
                estimated_duration_minutes=max(1, round(float(itinerary.get("duration") or 0) / 60)),
                estimated_price_eur=estimate.typical_eur, is_estimate=True, provider=PROVIDER_NAME,
                metadata={
                    "ticket_coverage": "estimated paid long-distance rail fare",
                    **estimate.as_dict(),
                    "planned_departure": itinerary.get("start"), "planned_arrival": itinerary.get("end"),
                    "transfer_count": itinerary.get("numberOfTransfers", 0),
                    "minimum_transfer_minutes_required": minimum_transfer_minutes,
                    "transfer_waits": waits,
                    "total_transfer_wait_minutes": sum(wait["wait_minutes"] or 0 for wait in waits),
                    "legs": [{
                        "line": leg.get("route", {}).get("shortName"), "route": leg.get("route", {}).get("longName"),
                        "operator": leg.get("route", {}).get("agency", {}).get("name"),
                        "from": leg.get("from", {}).get("name"), "to": leg.get("to", {}).get("name"),
                        "scheduled_departure": leg.get("start", {}).get("scheduledTime"),
                        "scheduled_arrival": leg.get("end", {}).get("scheduledTime"),
                    } for leg in legs],
                },
            ))
        return edges


async def discover_long_distance_train_edges(
    origin_nodes: list[TransportNode], destination_nodes: list[TransportNode], travel_date: str,
    departure_time: str, travellers: int, gateway_departures: dict[str, datetime] | None = None,
    minimum_transfer_minutes: int = 0, search_entire_day: bool = False,
) -> tuple[list[TravelEdge], list[dict[str, str]]]:
    """Discover domestic paid-long-distance candidates from the local official feed."""
    origins = [node for node in origin_nodes if node.type == "train_station"]
    destinations = [node for node in destination_nodes if node.type == "train_station"]
    if not origins or not destinations:
        return [], []
    try:
        default_departure = datetime.fromisoformat(f"{travel_date}T{departure_time}:00").replace(tzinfo=ZoneInfo("Europe/Berlin"))
    except ValueError:
        return [], [{"infrastructure_type": "long_distance_trains", "message": "The selected departure time is invalid."}]
    provider = OpenTripPlannerLongDistanceTrainProvider()
    edges: list[TravelEdge] = []
    try:
        for origin in origins:
            for destination in destinations:
                departure = (gateway_departures or {}).get(origin.id, default_departure)
                if search_entire_day:
                    departure = departure.replace(hour=0, minute=0)
                edges.extend(await provider.find_estimated_fare_edges(
                    origin, destination, travel_date, departure, minimum_transfer_minutes,
                    travellers, search_entire_day,
                ))
    except LongDistanceTrainProviderUnavailableError:
        return [], [{"infrastructure_type": "long_distance_trains", "message": "The local long-distance train planner is not available yet; paid train schedules could not be added."}]
    except LongDistanceTrainRoutingError as error:
        return [], [{"infrastructure_type": "long_distance_trains", "message": str(error)}]
    return list({edge.id: edge for edge in edges}.values()), []
