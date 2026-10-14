"""FastAPI entry point for discovery of long-distance transport graph nodes."""

import asyncio
import logging
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from backend.services.geocoding import geocode
from backend.services.infra import (
    discover_airports,
    discover_train_stations,
    explain_airport_filter,
    explain_train_station_filter,
    InfrastructureProviderUnavailableError,
    select_city_airports,
)
from backend.database.repository import CityNodeRepository
from backend.database.session import initialize_database
from backend.services.node_factory import create_city_transport_nodes

logger = logging.getLogger(__name__)

@asynccontextmanager
async def lifespan(_: FastAPI):
    """Initialize local persistence before the API begins serving requests."""
    initialize_database()
    yield


app = FastAPI(
    title="Travel Route Optimizer API",
    version="0.1.0",
    description="""
The Travel Route Optimizer API discovers the initial and final long-distance
transport nodes for a journey. It compares neither ticket availability nor final
prices yet; this version prepares the city-center, train-station, and airport
nodes that will later be connected in a route graph.

The API stores approved infrastructure in a persistent cache. A normal search
uses cached data when it is still valid. A search with `debug: true` deliberately
bypasses the cache and exposes the decisions made by the train and airport filters.
""",
    lifespan=lifespan,
)
city_node_repository = CityNodeRepository()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5500"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


class SearchRequest(BaseModel):
    """Defines the user input required to discover graph nodes for a journey."""

    origin: str = Field(
        min_length=2,
        description="Origin city used to create the initial long-distance nodes.",
        examples=["Erfurt"],
    )
    destination: str = Field(
        min_length=2,
        description="Final destination city used to create the destination nodes.",
        examples=["München"],
    )
    date: str = Field(
        description="Planned travel date in YYYY-MM-DD format. It is retained for future connection searches.",
        examples=["2026-10-10"],
    )
    debug: bool = Field(
        default=False,
        description="When true, bypasses the database cache and returns filter decisions and rejected candidates.",
    )


async def get_city_infrastructure(
    city: dict, debug: bool
) -> tuple[dict, bool, dict | None, list[dict[str, str]]]:
    """Return cached or discovered infrastructure without caching an incomplete external result."""
    infrastructure = None if debug else city_node_repository.get_infrastructure(
        city["source_id"]
    )
    cache_hit = infrastructure is not None
    debug_data = None
    warnings: list[dict[str, str]] = []

    if infrastructure is None:
        station_result, airport_result = await asyncio.gather(
            discover_train_stations(city["latitude"], city["longitude"]),
            discover_airports(city["latitude"], city["longitude"]),
            return_exceptions=True,
        )
        raw_stations = []
        raw_airports = []
        for infrastructure_type, result in (
            ("train_stations", station_result),
            ("airports", airport_result),
        ):
            if isinstance(result, Exception):
                logger.warning(
                    "Could not discover %s for %s: %s",
                    infrastructure_type,
                    city["city_name"],
                    result,
                )
                warnings.append(
                    {
                        "city": city["city_name"],
                        "infrastructure_type": infrastructure_type,
                        "message": "External infrastructure data is temporarily unavailable.",
                    }
                )
            elif infrastructure_type == "train_stations":
                raw_stations = result
            else:
                raw_airports = result

        station_filter = explain_train_station_filter(
            raw_stations, city["city_name"]
        )
        infrastructure = {
            "train_stations": station_filter["selected"],
            "airports": select_city_airports(raw_airports, city["city_name"]),
        }
        if debug:
            debug_data = {
                "cache_bypassed": True,
                "train_station_filter": station_filter,
                "airport_filter": explain_airport_filter(
                    raw_airports, city["city_name"]
                ),
            }
        elif not warnings:
            city_node_repository.save_infrastructure(city, infrastructure)
    return infrastructure, cache_hit, debug_data, warnings


@app.get(
    "/",
    summary="Check API availability",
    description="""
Returns a small health response when the API process is available.

This route does not call external APIs, create graph nodes, or read cached city infrastructure.
Use it to verify that FastAPI or its Docker container has started correctly.
""",
    response_description="Confirmation that the TRO API is running.",
)
def root():
    """Report that the backend service is available."""
    return {"message": "TRO API is running"}


@app.post(
    "/search",
    summary="Discover initial and final transport nodes",
    description="""
Creates the transport-node candidates needed before route calculation.

1. The API geocodes the origin and destination cities in parallel.
2. For each city, it checks the persistent database cache using the canonical geocoding identifier.
3. On a cache miss, it queries nearby railway stations and airports, applies the project filters, and saves the approved candidates.
4. It always creates a `city_center` node for car routes. It adds a `train_station` node only when the city-specific station filter succeeds and adds `airport` nodes only when the city airport filter succeeds.

The route does not yet calculate train, flight, or car connections between these nodes. Set `debug` to `true` only while validating filters: it refreshes data from external APIs instead of using the database cache. If a temporary infrastructure-provider failure occurs, the response still returns the city-center node and adds a `warnings` entry; incomplete data is never cached.
""",
    response_description="Origin and destination data, selected infrastructure, generated graph nodes, and cache status.",
    responses={
        404: {
            "description": "The origin or destination could not be geocoded as a city.",
        },
        503: {
            "description": "A geocoding or infrastructure provider is temporarily unavailable or rate limited.",
        },
        200: {
            "description": "Initial and final transport nodes were successfully discovered.",
            "content": {
                "application/json": {
                    "example": {
                        "origin_nodes": [
                            {
                                "id": "city_center:nominatim:relation:123",
                                "type": "city_center",
                                "name": "Erfurt",
                                "supported_modes": ["car"],
                            },
                            {
                                "id": "osm:node:2700123189",
                                "type": "train_station",
                                "name": "Erfurt Hauptbahnhof",
                                "supported_modes": ["train"],
                            },
                        ],
                        "cache": {"origin_hit": False, "destination_hit": False},
                    }
                }
            },
        },
    },
)
async def search(request: SearchRequest):
    """Geocode both cities and create their initial long-distance graph nodes."""
    try:
        origin_data, destination_data = await asyncio.gather(
            geocode(request.origin), geocode(request.destination)
        )

        if "error" in origin_data:
            raise HTTPException(status_code=404, detail="Origin city not found")
        if "error" in destination_data:
            raise HTTPException(status_code=404, detail="Destination city not found")

        origin_result, destination_result = await asyncio.gather(
            get_city_infrastructure(origin_data, request.debug),
            get_city_infrastructure(destination_data, request.debug),
        )
    except (httpx.HTTPError, InfrastructureProviderUnavailableError) as error:
        logger.warning("External provider unavailable while discovering city nodes: %s", error)
        raise HTTPException(
            status_code=503,
            detail="A location or infrastructure provider is temporarily unavailable. Please try again shortly.",
        ) from error

    origin_infrastructure, origin_cache_hit, origin_debug, origin_warnings = origin_result
    destination_infrastructure, destination_cache_hit, destination_debug, destination_warnings = destination_result

    response = {
        "origin": origin_data,
        "destination": destination_data,
        "origin_infrastructure": origin_infrastructure,
        "destination_infrastructure": destination_infrastructure,
        "origin_nodes": create_city_transport_nodes(
            origin_data, origin_infrastructure
        ),
        "destination_nodes": create_city_transport_nodes(
            destination_data, destination_infrastructure
        ),
        "cache": {
            "origin_hit": origin_cache_hit,
            "destination_hit": destination_cache_hit,
        },
    }
    warnings = origin_warnings + destination_warnings
    if warnings:
        response["warnings"] = warnings
    if request.debug:
        response["debug"] = {
            "origin": origin_debug,
            "destination": destination_debug,
        }
    return response
