"""Create Deutschlandticket-compatible train edges from local OpenTripPlanner data."""

import os
import re
from datetime import datetime
from zoneinfo import ZoneInfo

import httpx

from backend.models.graph import TravelEdge
from backend.models.node import TransportNode
from backend.services.regional_gtfs import find_gtfs_station_id

OTP_GTFS_URL = os.getenv("OTP_GTFS_URL", "http://localhost:8080/otp/gtfs/v1")
OTP_PROVIDER_NAME = "OpenTripPlanner with GTFS Regional Rail Germany"
# The input archive has already been filtered by build_d_ticket_gtfs.py. Keep a
# defensive rejection here for the few unambiguously long-distance labels, but
# do not require a DB-style RB/RE/S-Bahn prefix: regional operators also use
# valid names such as STB, ALX, MEX, or agilis.
_LONG_DISTANCE_LINE_PATTERN = re.compile(r"^(ICE|IC|EC|FLX|TGV|RJ|NJ)", re.IGNORECASE)


class RegionalTrainProviderUnavailableError(Exception):
    """Raised when the local regional-rail journey planner is not available."""


class RegionalTrainRoutingError(Exception):
    """Raised when OTP rejects a station pair instead of returning an empty timetable."""


def _meets_minimum_transfer_time(transfer_waits: list[dict], minimum_minutes: int) -> bool:
    """Accept an itinerary only when every scheduled train change meets the user's buffer."""
    return all(
        isinstance(transfer.get("wait_minutes"), int)
        and transfer["wait_minutes"] >= minimum_minutes
        for transfer in transfer_waits
    )


class OpenTripPlannerRegionalTrainProvider:
    """Query the locally hosted planner without exposing it directly to browser clients."""

    def __init__(self, base_url: str | None = None) -> None:
        """Use an injected URL in tests or the environment configuration in production."""
        self._base_url = (base_url or OTP_GTFS_URL).rstrip("/")

    async def _query(self, query: str, variables: dict | None = None) -> dict:
        """Execute one GraphQL request and convert network failures into provider errors."""
        try:
            async with httpx.AsyncClient(timeout=30) as client:
                response = await client.post(
                    self._base_url,
                    json={"query": query, "variables": variables or {}},
                )
                response.raise_for_status()
                return response.json()
        except (httpx.HTTPError, ValueError) as error:
            raise RegionalTrainProviderUnavailableError(
                "The local regional-train planner is unavailable."
            ) from error

    async def _planner_stop_location(self, node: TransportNode) -> dict:
        """Resolve an OSM/Nominatim station node to OTP's exact FeedId:StopId format."""
        feed_payload = await self._query("query { feeds { feedId } }")
        feeds = feed_payload.get("data", {}).get("feeds", [])
        if feed_payload.get("errors") or len(feeds) != 1:
            raise RegionalTrainProviderUnavailableError(
                "The regional planner did not expose one usable GTFS feed."
            )
        station_id = find_gtfs_station_id(node.name, node.latitude, node.longitude)
        if station_id is None:
            raise RegionalTrainRoutingError(
                f"No matching regional GTFS station was found for {node.name}."
            )
        return {"stopLocation": {"stopLocationId": f"{feeds[0]['feedId']}:{station_id}"}}

    @staticmethod
    def _is_d_ticket_eligible_leg(leg: dict) -> bool:
        """Trust the filtered regional feed while rejecting clear long-distance leftovers."""
        line = (leg.get("route", {}).get("shortName") or "").strip()
        operator = (leg.get("route", {}).get("agency", {}).get("name") or "").lower()
        if "flixtrain" in operator or "db fernverkehr" in operator:
            return False
        return not bool(_LONG_DISTANCE_LINE_PATTERN.match(line))

    async def find_d_ticket_edges(self, origin: TransportNode, destination: TransportNode, travel_date: str, departure_at: datetime, minimum_transfer_minutes: int) -> list[TravelEdge]:
        """Return regional itineraries as zero-marginal-cost D-Ticket graph edges."""
        if origin.type != "train_station" or destination.type != "train_station":
            return []
        origin_location = await self._planner_stop_location(origin)
        destination_location = await self._planner_stop_location(destination)
        query = """
        query RegionalRail($origin: PlanLabeledLocationInput!, $destination: PlanLabeledLocationInput!, $dateTime: PlanDateTimeInput!, $preferences: PlanPreferencesInput) {
          planConnection(origin: $origin, destination: $destination, dateTime: $dateTime, first: 50,
            searchWindow: "PT12H", preferences: $preferences,
            modes: { transitOnly: true, transit: { transit: [{ mode: RAIL }] } }) {
            edges { node { start end duration numberOfTransfers legs {
              mode transitLeg distance
              from { name lat lon } to { name lat lon }
              start { scheduledTime } end { scheduledTime }
              route { shortName longName agency { name } }
            } } }
            routingErrors { description }
          }
        }
        """
        variables = {
            "origin": {"label": origin.name, "location": origin_location},
            "destination": {"label": destination.name, "location": destination_location},
            "dateTime": {"earliestDeparture": departure_at.isoformat()},
            "preferences": (
                {"transit": {"transfer": {"slack": f"PT{minimum_transfer_minutes}M"}}}
                if minimum_transfer_minutes else None
            ),
        }
        payload = await self._query(query, variables)

        connection = payload.get("data", {}).get("planConnection", {})
        if payload.get("errors"):
            raise RegionalTrainProviderUnavailableError(
                "The local regional-train planner returned a GraphQL error."
            )
        if connection.get("routingErrors"):
            error = connection["routingErrors"][0]
            code = error.get("code", "UNKNOWN")
            description = error.get("description") or "no further details were provided"
            raise RegionalTrainRoutingError(
                f"The regional planner could not route the selected stations (OTP: {code}; {description})."
            )
        edges: list[TravelEdge] = []
        for index, result in enumerate(connection.get("edges", []), start=1):
            itinerary = result.get("node", {})
            legs = [leg for leg in itinerary.get("legs", []) if leg.get("transitLeg")]
            if not legs:
                continue
            if not all(self._is_d_ticket_eligible_leg(leg) for leg in legs):
                continue
            distance_km = round(sum(float(leg.get("distance") or 0) for leg in legs) / 1_000, 2)
            duration_minutes = max(1, round(float(itinerary.get("duration") or 0) / 60))
            transfer_waits = []
            for previous_leg, next_leg in zip(legs, legs[1:]):
                previous_arrival = previous_leg.get("end", {}).get("scheduledTime")
                next_departure = next_leg.get("start", {}).get("scheduledTime")
                wait_minutes = None
                if previous_arrival and next_departure:
                    try:
                        wait_minutes = max(0, round((datetime.fromisoformat(next_departure) - datetime.fromisoformat(previous_arrival)).total_seconds() / 60))
                    except ValueError:
                        pass
                transfer_waits.append({
                    "at_station": previous_leg.get("to", {}).get("name"),
                    "from_line": previous_leg.get("route", {}).get("shortName"),
                    "to_line": next_leg.get("route", {}).get("shortName"),
                    "scheduled_arrival": previous_arrival,
                    "scheduled_departure": next_departure,
                    "wait_minutes": wait_minutes,
                })
            if not _meets_minimum_transfer_time(transfer_waits, minimum_transfer_minutes):
                continue
            route_start = re.sub(r"[^0-9]", "", str(itinerary.get("start") or departure_at.isoformat()))
            edges.append(TravelEdge(
                id=f"train:d-ticket:{origin.id}:{destination.id}:{travel_date}:{route_start}:{index}",
                origin_node_id=origin.id, destination_node_id=destination.id,
                mode="train", edge_type="train_connection", estimated_distance_km=distance_km,
                estimated_duration_minutes=duration_minutes, estimated_price_eur=0,
                is_estimate=False, provider=OTP_PROVIDER_NAME,
                metadata={
                    "ticket_coverage": "Deutschlandticket",
                    "price_status": "covered_by_existing_ticket",
                    "planned_departure": itinerary.get("start"),
                    "planned_arrival": itinerary.get("end"),
                    "transfer_count": itinerary.get("numberOfTransfers", 0),
                    "minimum_transfer_minutes_required": minimum_transfer_minutes,
                    "transfer_waits": transfer_waits,
                    "total_transfer_wait_minutes": sum(wait["wait_minutes"] or 0 for wait in transfer_waits),
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


async def discover_regional_train_edges(origin_nodes: list[TransportNode], destination_nodes: list[TransportNode], travel_date: str, departure_time: str, has_deutschlandticket: bool, gateway_departures: dict[str, datetime] | None = None, minimum_transfer_minutes: int = 0) -> tuple[list[TravelEdge], list[dict[str, str]]]:
    """Discover D-Ticket routes only when the traveller confirms ticket ownership."""
    if not has_deutschlandticket:
        return [], []
    provider = OpenTripPlannerRegionalTrainProvider()
    origin_stations = [node for node in origin_nodes if node.type == "train_station"]
    destination_stations = [node for node in destination_nodes if node.type == "train_station"]
    if not origin_stations:
        return [], [{
            "infrastructure_type": "regional_trains",
            "message": "No eligible origin train-station node is available for a Deutschlandticket search.",
        }]
    if not destination_stations:
        return [], [{
            "infrastructure_type": "regional_trains",
            "message": "No eligible destination train-station node is available for a Deutschlandticket search.",
        }]
    edges: list[TravelEdge] = []
    try:
        default_departure = datetime.fromisoformat(f"{travel_date}T{departure_time}:00").replace(tzinfo=ZoneInfo("Europe/Berlin"))
    except ValueError:
        return [], [{"infrastructure_type": "regional_trains", "message": "The selected departure time is invalid."}]
    for origin_station in origin_stations:
        for destination_station in destination_stations:
            try:
                station_departure = (gateway_departures or {}).get(origin_station.id, default_departure)
                # OTP receives both the requested transfer buffer and a twelve-hour
                # search window, so it can construct comfortable later departures.
                edges.extend(await provider.find_d_ticket_edges(
                    origin_station, destination_station, travel_date,
                    station_departure, minimum_transfer_minutes,
                ))
            except RegionalTrainProviderUnavailableError:
                return [], [{"infrastructure_type": "regional_trains", "message": "The regional-train planner is not available yet; D-Ticket routes could not be added."}]
            except RegionalTrainRoutingError as error:
                return [], [{"infrastructure_type": "regional_trains", "message": str(error)}]
    unique_edges = {edge.id: edge for edge in edges}
    edges = list(unique_edges.values())
    if not edges and minimum_transfer_minutes:
        return [], [{
            "infrastructure_type": "regional_trains",
            "message": f"No regional route met the selected minimum transfer time of {minimum_transfer_minutes} minutes.",
        }]
    return edges, []
