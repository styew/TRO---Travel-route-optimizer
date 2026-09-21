import asyncio

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from backend.services.geocoding import geocode
from backend.services.infra import (
    discover_airports,
    discover_train_stations,
    select_city_train_station,
)
from backend.services.infrastructure_cache import InfrastructureCache

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
    origin: str
    destination: str
    date: str


@app.get("/")
def root():
    return {"message": "TRO API is running"}


@app.post("/search")
async def search(request: SearchRequest):
    origin_data, destination_data = await asyncio.gather(
        geocode(request.origin), geocode(request.destination)
    )

    if "error" in origin_data:
        raise HTTPException(status_code=404, detail="Origin city not found")
    if "error" in destination_data:
        raise HTTPException(status_code=404, detail="Destination city not found")

    origin_infra = infrastructure_cache.get(
        origin_data["latitude"], origin_data["longitude"]
    )
    cache_hit = origin_infra is not None

    if origin_infra is None:
        raw_stations, airports = await asyncio.gather(
            discover_train_stations(origin_data["latitude"], origin_data["longitude"]),
            discover_airports(origin_data["latitude"], origin_data["longitude"]),
        )
        origin_infra = {
            "train_stations": select_city_train_station(
                raw_stations, origin_data["city_name"]
            ),
            "airports": airports,
        }
        infrastructure_cache.save(
            origin_data["latitude"], origin_data["longitude"], origin_infra
        )

    return {
        "origin": origin_data,
        "destination": destination_data,
        "origin_infrastructure": origin_infra,
        "cache_hit": cache_hit,
    }
