"""FastAPI entry point for discovery of long-distance transport graph nodes."""

import asyncio

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from backend.services.geocoding import geocode
from backend.services.infra import (
    discover_airports,
    discover_train_stations,
    explain_airport_filter,
    explain_train_station_filter,
    select_city_airports,
)
from backend.services.infrastructure_cache import InfrastructureCache
from backend.services.node_factory import create_city_transport_nodes

app = FastAPI()
infrastructure_cache = InfrastructureCache()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5500"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


class SearchRequest(BaseModel):
    """Defines the user input required to discover graph nodes for a journey."""

    origin: str
    destination: str
    date: str
    debug: bool = False


async def get_city_infrastructure(city: dict, debug: bool) -> tuple[dict, bool, dict | None]:
    """Return approved station and airport candidates for one city, using cache when allowed."""
    infrastructure = None if debug else infrastructure_cache.get(
        city["latitude"], city["longitude"]
    )
    cache_hit = infrastructure is not None
    debug_data = None

    if infrastructure is None:
        raw_stations, raw_airports = await asyncio.gather(
            discover_train_stations(city["latitude"], city["longitude"]),
            discover_airports(city["latitude"], city["longitude"]),
        )
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
        else:
            infrastructure_cache.save(
                city["latitude"], city["longitude"], infrastructure
            )
    return infrastructure, cache_hit, debug_data


@app.get("/")
def root():
    """Report that the backend service is available."""
    return {"message": "TRO API is running"}


@app.post("/search")
async def search(request: SearchRequest):
    """Geocode both cities and create their initial long-distance graph nodes."""
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
    origin_infrastructure, origin_cache_hit, origin_debug = origin_result
    destination_infrastructure, destination_cache_hit, destination_debug = destination_result

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
    if request.debug:
        response["debug"] = {
            "origin": origin_debug,
            "destination": destination_debug,
        }
    return response
