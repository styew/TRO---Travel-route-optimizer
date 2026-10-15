"""Recover essential city terminals when a public Overpass instance is unavailable."""

from __future__ import annotations

from typing import Literal

import httpx

from backend.services.infra import _distance_km

NOMINATIM_SEARCH_URL = "https://nominatim.openstreetmap.org/search"
NOMINATIM_USER_AGENT = "TRO-Travel-Route-Optimizer"


async def _search_nominatim(query: str) -> list[dict]:
    """Search a small, specific terminal query without using Nominatim as autocomplete."""
    async with httpx.AsyncClient(timeout=15.0) as client:
        response = await client.get(
            NOMINATIM_SEARCH_URL,
            params={
                "q": query,
                "format": "jsonv2",
                "limit": 3,
                "namedetails": 1,
                "extratags": 1,
            },
            headers={"User-Agent": NOMINATIM_USER_AGENT},
        )
        response.raise_for_status()
        return response.json()


def _candidate_from_result(
    result: dict,
    latitude: float,
    longitude: float,
    terminal_type: Literal["train_station", "airport"],
    max_distance_km: float,
) -> dict | None:
    """Normalize one Nominatim result only when it remains near the requested city."""
    try:
        result_latitude = float(result["lat"])
        result_longitude = float(result["lon"])
    except (KeyError, TypeError, ValueError):
        return None
    distance_km = _distance_km(
        latitude, longitude, result_latitude, result_longitude
    )
    if distance_km > max_distance_km:
        return None
    name = (
        result.get("namedetails", {}).get("name")
        or result.get("display_name", "").split(",")[0]
    )
    if not name:
        return None
    tags = result.get("extratags", {})
    return {
        "id": f"nominatim:{result.get('osm_type')}:{result.get('osm_id')}",
        "name": name,
        "latitude": result_latitude,
        "longitude": result_longitude,
        "type": terminal_type,
        "distance_km": round(distance_km, 2),
        "iata": tags.get("iata"),
        "icao": tags.get("icao"),
        "scheduled_service": tags.get("scheduled_service"),
        "passenger": tags.get("passenger"),
        "wikidata": tags.get("wikidata"),
        "fallback_terminal_type": terminal_type,
        "source": "nominatim-fallback",
    }


async def discover_terminal_fallback(
    city_name: str,
    latitude: float,
    longitude: float,
    terminal_type: Literal["train_station", "airport"],
) -> list[dict]:
    """Find one nearby main station or commercial airport after an Overpass outage."""
    queries = (
        [f"{city_name} Hauptbahnhof"]
        if terminal_type == "train_station"
        else [f"{city_name} airport", f"{city_name} aerodrome"]
    )
    max_distance_km = 20 if terminal_type == "train_station" else 50
    for query in queries:
        candidates = []
        for result in await _search_nominatim(query):
            candidate = _candidate_from_result(
                result, latitude, longitude, terminal_type, max_distance_km
            )
            if candidate is not None:
                candidates.append(candidate)
        if candidates:
            # A search can return several airports; keep their order by proximity
            # for the generic commercial-airport ranking performed afterwards.
            return sorted(candidates, key=lambda candidate: candidate["distance_km"])
    return []
