"""Routes that discover city infrastructure and assemble the initial route graph."""

import asyncio
import logging
from datetime import datetime
from zoneinfo import ZoneInfo

import httpx
from fastapi import APIRouter, HTTPException

from backend.api.schemas.search import SearchRequest
from backend.database.repository import CityNodeRepository, GatewayAirportRepository
from backend.models.graph import GraphPolicy
from backend.services.geocoding import geocode
from backend.services.flight_provider import discover_flight_edges
from backend.services.flight_schedule_provider import discover_scheduled_flight_edges
from backend.services.graph_builder import (
    build_initial_route_graph,
    create_gateway_airport_nodes,
    create_gateway_station_nodes,
    gateway_train_departure_time,
)
from backend.services.commercial_train_provider import discover_commercial_train_edges
from backend.services.airport_codes import enrich_airport_codes
from backend.services.infra import (
    InfrastructureProviderUnavailableError,
    discover_airports,
    discover_gateway_airports,
    discover_gateway_train_stations,
    discover_train_stations,
    explain_airport_filter,
    explain_train_station_filter,
    select_city_airports,
    select_gateway_airports,
)
from backend.services.node_factory import create_city_transport_nodes
from backend.services.nominatim_infrastructure import discover_terminal_fallback
from backend.services.regional_train_provider import discover_regional_train_edges
from backend.services.long_distance_train_provider import discover_long_distance_train_edges
from backend.services.international_rail_provider import discover_international_rail_edges
from backend.services.rail_network_catalog import (
    corridor_gateway_nodes,
    select_rail_corridor,
)
from backend.services.route_planner import find_lowest_price_routes

logger = logging.getLogger(__name__)
router = APIRouter(tags=["Graph discovery"])
city_node_repository = CityNodeRepository()
gateway_airport_repository = GatewayAirportRepository()


async def get_city_infrastructure(city: dict, debug: bool) -> tuple[dict, bool, dict | None, list[dict[str, str]]]:
    """Return cached or discovered infrastructure without caching incomplete results."""
    infrastructure = None if debug else city_node_repository.get_infrastructure(city["source_id"])
    cache_hit = infrastructure is not None
    debug_data = None
    warnings: list[dict[str, str]] = []
    if infrastructure is None:
        station_result, airport_result = await asyncio.gather(
            discover_train_stations(city["latitude"], city["longitude"]),
            discover_airports(city["latitude"], city["longitude"]),
            return_exceptions=True,
        )
        raw_stations, raw_airports = [], []
        for infrastructure_type, result in (("train_stations", station_result), ("airports", airport_result)):
            if isinstance(result, Exception):
                logger.warning("Could not discover %s for %s: %s", infrastructure_type, city["city_name"], result)
                terminal_type = "train_station" if infrastructure_type == "train_stations" else "airport"
                try:
                    fallback = await discover_terminal_fallback(
                        city["city_name"], city["latitude"], city["longitude"], terminal_type
                    )
                except httpx.HTTPError:
                    fallback = []
                if fallback:
                    if infrastructure_type == "train_stations":
                        raw_stations = fallback
                    else:
                        raw_airports = fallback
                    warnings.append({"city": city["city_name"], "infrastructure_type": infrastructure_type, "message": "Overpass was unavailable; a Nominatim fallback terminal was used."})
                else:
                    warnings.append({"city": city["city_name"], "infrastructure_type": infrastructure_type, "message": "External infrastructure data is temporarily unavailable."})
            elif infrastructure_type == "train_stations":
                raw_stations = result
            else:
                raw_airports = result
        # Airport codes are necessary to ask a flight provider for an airport pair.
        raw_airports = await enrich_airport_codes(raw_airports)
        station_filter = explain_train_station_filter(raw_stations, city["city_name"])
        infrastructure = {"train_stations": station_filter["selected"], "airports": select_city_airports(raw_airports, city["city_name"])}
        if debug:
            debug_data = {"cache_bypassed": True, "train_station_filter": station_filter, "airport_filter": explain_airport_filter(raw_airports, city["city_name"])}
        unresolved_infrastructure = any(
            warning["message"] == "External infrastructure data is temporarily unavailable."
            for warning in warnings
        )
        if not unresolved_infrastructure:
            city_node_repository.save_infrastructure(city, infrastructure)
    return infrastructure, cache_hit, debug_data, warnings


@router.post(
    "/search",
    summary="Discover initial graph nodes and estimated car edges",
    description="""Geocodes origin and destination, loads or discovers city infrastructure, and assembles a serializable graph.

The graph always contains an estimated direct-car edge between city centers. When the origin has no local eligible train station, it may contain estimated car-access edges to nearby main-station gateways. With `respect_departure_time` enabled, `departure_time` sets the local journey start, trains are queried after estimated car access plus a ten-minute station buffer, and car-to-flight paths require a ninety-minute airport-arrival buffer. Disable it to compare connections available across the selected day without rejecting paths because of the chosen start time. When airport nodes have IATA codes, TRO loads a permitted cached published schedule from AeroDataBox and refreshes only missing airport/day records. Scheduled flights include airline, flight number and timings, while their prices remain explicitly estimated from historic observations or the local model. Optional Duffel offers and Travelpayouts cached price references can supplement these schedule edges. It may also compare up to three nearby IATA airport gateways reachable by an estimated car journey, so a cheaper gateway flight can compete with a local-airport flight. If `has_deutschlandticket` is true and the local OpenTripPlanner service is available, it adds regional-rail itineraries with zero marginal ticket cost and scheduled waiting times for every train transfer. For configured international corridors, it can continue from a bounded German gateway station onto separately priced international rail; the D-Ticket segment remains zero marginal cost and the onward rail fare is explicitly estimated. The API ranks up to ten complete, cycle-free paths by lowest estimated price. `max_transport_categories` limits changes between car, train, flight, and ferry; consecutive train legs count as one category.""",
    response_description="City data, selected infrastructure, nodes, estimated car edges, and cache status.",
    responses={404: {"description": "The origin or destination could not be geocoded as a city."}, 503: {"description": "A geocoding provider is temporarily unavailable or rate limited."}, 200: {"description": "Initial graph nodes and available estimated car edges were discovered."}},
)
async def search(request: SearchRequest):
    """Geocode both cities, discover terminal nodes, and assemble the initial graph."""
    try:
        origin_data, destination_data = await asyncio.gather(geocode(request.origin), geocode(request.destination))
    except httpx.HTTPError as error:
        logger.warning("Geocoding provider unavailable while discovering city nodes: %s", error)
        raise HTTPException(status_code=503, detail="The location provider is temporarily unavailable. Please try again shortly.") from error
    if "error" in origin_data:
        raise HTTPException(status_code=404, detail="Origin city not found")
    if "error" in destination_data:
        raise HTTPException(status_code=404, detail="Destination city not found")

    origin_result, destination_result = await asyncio.gather(get_city_infrastructure(origin_data, request.debug), get_city_infrastructure(destination_data, request.debug))
    origin_infrastructure, origin_cache_hit, origin_debug, origin_warnings = origin_result
    destination_infrastructure, destination_cache_hit, destination_debug, destination_warnings = destination_result
    origin_nodes = create_city_transport_nodes(origin_data, origin_infrastructure, "origin")
    destination_nodes = create_city_transport_nodes(destination_data, destination_infrastructure, "destination")

    gateway_nodes, gateway_airport_nodes = [], []
    graph_warnings, gateway_candidates, gateway_airport_candidates = [], [], []
    local_station_data_available = not any(warning["infrastructure_type"] == "train_stations" for warning in origin_warnings)
    if not origin_infrastructure["train_stations"] and local_station_data_available:
        try:
            gateway_candidates = await discover_gateway_train_stations(origin_data["latitude"], origin_data["longitude"])
            gateway_nodes = create_gateway_station_nodes(gateway_candidates)
        except (httpx.HTTPError, InfrastructureProviderUnavailableError) as error:
            logger.warning("Could not discover gateway stations for %s: %s", origin_data["city_name"], error)
            graph_warnings.append({"city": origin_data["city_name"], "infrastructure_type": "gateway_train_stations", "message": "Nearby main stations could not be discovered; only the direct car edge is available."})

    graph_policy = GraphPolicy(max_transport_categories=request.max_transport_categories)
    origin_center = next(node for node in origin_nodes if node.type == "city_center")
    destination_center = next(node for node in destination_nodes if node.type == "city_center")
    # Only a configured country corridor receives international gateway nodes.
    # This keeps the normal German search small and avoids loading worldwide
    # schedules when the user is not leaving Germany.
    international_corridor = (
        select_rail_corridor(origin_data.get("country_code"), destination_data.get("country_code"))
        if request.has_deutschlandticket else None
    )
    # The European timetable archive, not a hard-coded city list, supplies
    # German long-distance interchange candidates near this origin.
    international_gateway_nodes = corridor_gateway_nodes(
        international_corridor, origin_center
    )
    train_search_time = request.departure_time if request.respect_departure_time else "00:00"
    gateway_departures = {
        gateway.id: gateway_train_departure_time(
            origin_center, gateway, request.date, train_search_time, graph_policy
        )
        for gateway in gateway_nodes
    }
    (
        flight_result,
        d_ticket_result,
        d_ticket_gateway_result,
        long_distance_train_result,
        international_rail_result,
        commercial_train_result,
    ) = await asyncio.gather(
        discover_flight_edges(origin_nodes, destination_nodes, request.date, request.travellers),
        discover_regional_train_edges(
            origin_nodes + gateway_nodes, destination_nodes, request.date,
            train_search_time, request.has_deutschlandticket, gateway_departures,
            request.minimum_train_transfer_minutes, not request.respect_departure_time,
        ),
        discover_regional_train_edges(
            origin_nodes + gateway_nodes, international_gateway_nodes, request.date,
            train_search_time, request.has_deutschlandticket, gateway_departures,
            request.minimum_train_transfer_minutes, not request.respect_departure_time,
        ),
        discover_long_distance_train_edges(
            origin_nodes + gateway_nodes, destination_nodes, request.date,
            train_search_time, request.travellers, gateway_departures,
            request.minimum_train_transfer_minutes, not request.respect_departure_time,
        ),
        discover_international_rail_edges(
            international_corridor, international_gateway_nodes, destination_center,
            request.date, train_search_time, request.travellers,
            request.minimum_train_transfer_minutes, not request.respect_departure_time,
        ),
        discover_commercial_train_edges(
            origin_nodes + gateway_nodes, destination_nodes, request.date,
            request.departure_time, request.has_deutschlandticket, gateway_departures,
        ),
    )
    flight_edges, flight_warnings = flight_result
    train_edges, train_warnings = d_ticket_result
    d_ticket_gateway_edges, d_ticket_gateway_warnings = d_ticket_gateway_result
    long_distance_train_edges, long_distance_train_warnings = long_distance_train_result
    international_rail_edges, international_rail_warnings, international_rail_diagnostics = international_rail_result
    commercial_train_edges, commercial_train_warnings = commercial_train_result
    scheduled_flight_edges, schedule_warnings = await discover_scheduled_flight_edges(
        origin_nodes, destination_nodes, request.date
    )
    flight_edges.extend(scheduled_flight_edges)
    gateway_scheduled_edges: list = []

    # Airport gateways are a bounded comparison pass. It supports a city with
    # no usable airport (Mannheim) and a cheaper gateway flight (Memmingen)
    # without querying an unbounded list of German airports.
    destination_has_iata_airport = any(
        node.type == "airport" and node.metadata.get("iata")
        for node in destination_nodes
    )
    local_flight_edge_count = len(flight_edges)
    if destination_has_iata_airport:
        try:
            raw_gateway_airports = await discover_gateway_airports(
                origin_data["latitude"], origin_data["longitude"]
            )
            raw_gateway_airports = await enrich_airport_codes(raw_gateway_airports)
            local_airport_ids = {
                node.id for node in origin_nodes if node.type == "airport"
            }
            gateway_airport_candidates = select_gateway_airports(
                raw_gateway_airports, local_airport_ids
            )
            if gateway_airport_candidates:
                gateway_airport_repository.save_candidates(
                    origin_data["source_id"], gateway_airport_candidates
                )
            else:
                gateway_airport_candidates = gateway_airport_repository.get_candidates(
                    origin_data["source_id"]
                )
                if gateway_airport_candidates:
                    graph_warnings.append({
                        "city": origin_data["city_name"],
                        "infrastructure_type": "airport_gateways",
                        "message": "No new nearby airport gateway was returned; stored gateway candidates are being used.",
                    })
            gateway_airport_nodes = create_gateway_airport_nodes(
                gateway_airport_candidates
            )
            if gateway_airport_nodes:
                gateway_flight_edges, gateway_flight_warnings = await discover_flight_edges(
                    gateway_airport_nodes, destination_nodes, request.date, request.travellers
                )
                flight_edges.extend(gateway_flight_edges)
                flight_warnings.extend(gateway_flight_warnings)
                gateway_scheduled_edges, gateway_schedule_warnings = await discover_scheduled_flight_edges(
                    gateway_airport_nodes, destination_nodes, request.date
                )
                flight_edges.extend(gateway_scheduled_edges)
                schedule_warnings.extend(gateway_schedule_warnings)
                if not gateway_flight_edges and not gateway_scheduled_edges and not local_flight_edge_count and not scheduled_flight_edges:
                    graph_warnings.append({
                        "city": origin_data["city_name"],
                        "infrastructure_type": "airport_gateways",
                        "message": "Nearby airport gateways were checked, but no cached flight reference was found for the selected date.",
                    })
        except (httpx.HTTPError, InfrastructureProviderUnavailableError) as error:
            logger.warning("Could not discover airport gateways for %s: %s", origin_data["city_name"], error)
            gateway_airport_candidates = gateway_airport_repository.get_candidates(
                origin_data["source_id"]
            )
            gateway_airport_nodes = create_gateway_airport_nodes(
                gateway_airport_candidates
            )
            graph_warnings.append({
                "city": origin_data["city_name"],
                "infrastructure_type": "airport_gateways",
                "message": "Nearby airport gateways could not be discovered; stored gateway candidates are being used when available.",
            })
    try:
        journey_departure = datetime.fromisoformat(f"{request.date}T{request.departure_time}:00").replace(tzinfo=ZoneInfo("Europe/Berlin")) if request.respect_departure_time else None
    except ValueError:
        raise HTTPException(status_code=422, detail="departure_time must use HH:MM.")
    graph = build_initial_route_graph(
        origin_nodes,
        destination_nodes,
        gateway_nodes,
        gateway_airports=gateway_airport_nodes,
        international_gateway_stations=international_gateway_nodes,
        additional_edges=(
            flight_edges + train_edges + d_ticket_gateway_edges
            + long_distance_train_edges + international_rail_edges + commercial_train_edges
        ),
        policy=graph_policy,
        departure_at=journey_departure,
    )
    route_plan = find_lowest_price_routes(
        graph, respect_departure_time=request.respect_departure_time
    )
    response = {"origin": origin_data, "destination": destination_data, "origin_infrastructure": origin_infrastructure, "destination_infrastructure": destination_infrastructure, "origin_nodes": origin_nodes, "destination_nodes": destination_nodes, "graph": graph, "route_plan": route_plan, "cache": {"origin_hit": origin_cache_hit, "destination_hit": destination_cache_hit}}
    warnings = (
        origin_warnings + destination_warnings + graph_warnings + flight_warnings
        + schedule_warnings + train_warnings + d_ticket_gateway_warnings
        + long_distance_train_warnings + international_rail_warnings + commercial_train_warnings
    )
    if warnings:
        response["warnings"] = warnings
    if request.debug:
        response["debug"] = {
            "origin": origin_debug,
            "destination": destination_debug,
            "gateway_station_candidates": gateway_candidates,
            "gateway_airport_candidates": gateway_airport_candidates,
            "scheduled_flights": {
                "local_edges": len(scheduled_flight_edges),
                "gateway_edges": len(gateway_scheduled_edges),
            },
            "regional_train_search": {
                "enabled": request.has_deutschlandticket,
                "origin_station_nodes": [node.name for node in origin_nodes + gateway_nodes if node.type == "train_station"],
                "destination_station_nodes": [node.name for node in destination_nodes if node.type == "train_station"],
                "minimum_transfer_minutes": request.minimum_train_transfer_minutes,
                "search_entire_day": not request.respect_departure_time,
            },
            "international_rail_search": {
                "corridor": international_corridor.key if international_corridor else None,
                "gateway_nodes": [node.name for node in international_gateway_nodes],
                "d_ticket_access_edge_count": len(d_ticket_gateway_edges),
                "paid_international_edge_count": len(international_rail_edges),
                "price_note": "Only the paid international segment uses a rail-price estimate.",
            },
            "long_distance_train_search": {
                "origin_station_nodes": [node.name for node in origin_nodes + gateway_nodes if node.type == "train_station"],
                "destination_station_nodes": [node.name for node in destination_nodes if node.type == "train_station"],
                "edge_count": len(long_distance_train_edges),
                "price_note": "Fares are local statistical estimates, not live offers.",
            },
        }
    if request.include_diagnostics:
        response["rail_diagnostics"] = {
            "international": international_rail_diagnostics,
            "regional_d_ticket_edge_count": len(d_ticket_gateway_edges),
            "domestic_long_distance_edge_count": len(long_distance_train_edges),
            "complete_route_count": len(route_plan.routes),
            "note": "Diagnostics describe search stages; they do not refresh city infrastructure or bypass the database cache.",
        }
    return response
