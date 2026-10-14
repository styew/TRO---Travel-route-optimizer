"""Routes that discover city infrastructure and assemble the initial route graph."""

import asyncio
import logging

import httpx
from fastapi import APIRouter, HTTPException

from backend.api.schemas.search import SearchRequest
from backend.database.repository import CityNodeRepository
from backend.services.geocoding import geocode
from backend.services.graph_builder import build_initial_route_graph, create_gateway_station_nodes
from backend.services.infra import (
    InfrastructureProviderUnavailableError,
    discover_airports,
    discover_gateway_train_stations,
    discover_train_stations,
    explain_airport_filter,
    explain_train_station_filter,
    select_city_airports,
)
from backend.services.node_factory import create_city_transport_nodes

logger = logging.getLogger(__name__)
router = APIRouter(tags=["Graph discovery"])
city_node_repository = CityNodeRepository()


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
                warnings.append({"city": city["city_name"], "infrastructure_type": infrastructure_type, "message": "External infrastructure data is temporarily unavailable."})
            elif infrastructure_type == "train_stations":
                raw_stations = result
            else:
                raw_airports = result
        station_filter = explain_train_station_filter(raw_stations, city["city_name"])
        infrastructure = {"train_stations": station_filter["selected"], "airports": select_city_airports(raw_airports, city["city_name"])}
        if debug:
            debug_data = {"cache_bypassed": True, "train_station_filter": station_filter, "airport_filter": explain_airport_filter(raw_airports, city["city_name"])}
        elif not warnings:
            city_node_repository.save_infrastructure(city, infrastructure)
    return infrastructure, cache_hit, debug_data, warnings


@router.post(
    "/search",
    summary="Discover initial graph nodes and estimated car edges",
    description="""Geocodes origin and destination, loads or discovers city infrastructure, and assembles a serializable graph.

The graph always contains an estimated direct-car edge between city centers. When the origin has no local eligible train station, it may contain estimated car-access edges to nearby main-station gateways. Train and flight connection edges will be added only after their timetable providers are integrated. Car values are transparent estimates, not live routing results.""",
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

    gateway_nodes, graph_warnings, gateway_candidates = [], [], []
    local_station_data_available = not any(warning["infrastructure_type"] == "train_stations" for warning in origin_warnings)
    if not origin_infrastructure["train_stations"] and local_station_data_available:
        try:
            gateway_candidates = await discover_gateway_train_stations(origin_data["latitude"], origin_data["longitude"])
            gateway_nodes = create_gateway_station_nodes(gateway_candidates)
        except (httpx.HTTPError, InfrastructureProviderUnavailableError) as error:
            logger.warning("Could not discover gateway stations for %s: %s", origin_data["city_name"], error)
            graph_warnings.append({"city": origin_data["city_name"], "infrastructure_type": "gateway_train_stations", "message": "Nearby main stations could not be discovered; only the direct car edge is available."})

    graph = build_initial_route_graph(origin_nodes, destination_nodes, gateway_nodes)
    response = {"origin": origin_data, "destination": destination_data, "origin_infrastructure": origin_infrastructure, "destination_infrastructure": destination_infrastructure, "origin_nodes": origin_nodes, "destination_nodes": destination_nodes, "graph": graph, "cache": {"origin_hit": origin_cache_hit, "destination_hit": destination_cache_hit}}
    warnings = origin_warnings + destination_warnings + graph_warnings
    if warnings:
        response["warnings"] = warnings
    if request.debug:
        response["debug"] = {"origin": origin_debug, "destination": destination_debug, "gateway_station_candidates": gateway_candidates}
    return response
