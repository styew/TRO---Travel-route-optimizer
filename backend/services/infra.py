"""Discovery, normalization, and selection of transport infrastructure."""

from __future__ import annotations

import asyncio
import logging
import math
import re
import time
import unicodedata
from typing import Any

import httpx

# Public instances occasionally time out independently. These endpoints use
# different operators, so a retry can survive an outage of the main service.
OVERPASS_URLS = (
    "https://overpass.private.coffee/api/interpreter",
    "https://maps.mail.ru/osm/tools/overpass/api/interpreter",
    "https://overpass-api.de/api/interpreter",
)
TRAIN_RADIUS_METERS = 20_000
AIRPORT_RADIUS_METERS = 50_000
GATEWAY_TRAIN_RADIUS_METERS = 100_000
OVERPASS_MIN_REQUEST_INTERVAL_SECONDS = 1.0
OVERPASS_MAX_RETRIES = 3
OVERPASS_RETRYABLE_STATUS_CODES = {429, 500, 502, 503, 504}

_overpass_request_lock = asyncio.Lock()
_last_overpass_request_at = 0.0
logger = logging.getLogger(__name__)


class InfrastructureProviderUnavailableError(RuntimeError):
    """Raised when Overpass remains unavailable after controlled retry attempts."""


def _normalize_text(value: str) -> str:
    value = unicodedata.normalize("NFKD", value)
    value = "".join(char for char in value if not unicodedata.combining(char))
    return re.sub(r"[^a-z0-9]+", " ", value.lower()).strip()


def _distance_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    radius_km = 6_371.0088
    lat_delta = math.radians(lat2 - lat1)
    lon_delta = math.radians(lon2 - lon1)
    a = math.sin(lat_delta / 2) ** 2 + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(lon_delta / 2) ** 2
    return radius_km * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


async def _query_overpass(query: str) -> list[dict[str, Any]]:
    """Query Overpass with pacing and fail over to another public instance on transient errors."""
    global _last_overpass_request_at

    headers = {"User-Agent": "TRO-Travel-Route-Optimizer"}
    async with _overpass_request_lock:
        elapsed = time.monotonic() - _last_overpass_request_at
        if elapsed < OVERPASS_MIN_REQUEST_INTERVAL_SECONDS:
            await asyncio.sleep(OVERPASS_MIN_REQUEST_INTERVAL_SECONDS - elapsed)

        async with httpx.AsyncClient(timeout=30.0) as client:
            for attempt in range(OVERPASS_MAX_RETRIES):
                endpoint = OVERPASS_URLS[attempt % len(OVERPASS_URLS)]
                _last_overpass_request_at = time.monotonic()
                try:
                    response = await client.post(
                        endpoint,
                        data={"data": query},
                        headers=headers,
                    )
                except httpx.RequestError as error:
                    logger.warning(
                        "Overpass request to %s failed on attempt %s of %s: %s",
                        endpoint,
                        attempt + 1,
                        OVERPASS_MAX_RETRIES,
                        error,
                    )
                    if attempt == OVERPASS_MAX_RETRIES - 1:
                        raise InfrastructureProviderUnavailableError(
                            "The infrastructure provider could not be reached."
                        ) from error
                    await asyncio.sleep(2 ** attempt)
                    continue

                if response.status_code in OVERPASS_RETRYABLE_STATUS_CODES:
                    logger.warning(
                        "Overpass endpoint %s returned HTTP %s on attempt %s of %s.",
                        endpoint,
                        response.status_code,
                        attempt + 1,
                        OVERPASS_MAX_RETRIES,
                    )
                    if attempt == OVERPASS_MAX_RETRIES - 1:
                        reason = (
                            "temporarily rate limited"
                            if response.status_code == 429
                            else "temporarily unavailable or timed out"
                        )
                        raise InfrastructureProviderUnavailableError(
                            f"The infrastructure provider is {reason}."
                        )
                    await asyncio.sleep(2 ** attempt)
                    continue

                response.raise_for_status()
                return response.json().get("elements", [])

    raise InfrastructureProviderUnavailableError(
        "The infrastructure provider did not return a usable response."
    )


def _element_coordinates(element: dict[str, Any]) -> tuple[float, float] | None:
    if "lat" in element and "lon" in element:
        return float(element["lat"]), float(element["lon"])
    center = element.get("center")
    if center and "lat" in center and "lon" in center:
        return float(center["lat"]), float(center["lon"])
    return None


def _normalize_elements(elements: list[dict[str, Any]], origin_latitude: float, origin_longitude: float, infrastructure_type: str) -> list[dict[str, Any]]:
    normalized = []
    for element in elements:
        tags = element.get("tags", {})
        coordinates = _element_coordinates(element)
        name = tags.get("name")
        if not name or not coordinates:
            continue
        latitude, longitude = coordinates
        normalized.append({
            "id": f"osm:{element['type']}:{element['id']}",
            "name": name,
            "latitude": latitude,
            "longitude": longitude,
            "type": infrastructure_type,
            "distance_km": round(_distance_km(origin_latitude, origin_longitude, latitude, longitude), 2),
            "iata": tags.get("iata"),
            "icao": tags.get("icao"),
            "scheduled_service": tags.get("scheduled_service"),
            "passenger": tags.get("passenger"),
            "source": "openstreetmap-overpass",
        })
    return sorted(normalized, key=lambda item: item["distance_km"])


async def discover_train_stations(latitude: float, longitude: float) -> list[dict[str, Any]]:
    query = f'''[out:json][timeout:25];
    (node["railway"="station"](around:{TRAIN_RADIUS_METERS},{latitude},{longitude});
     way["railway"="station"](around:{TRAIN_RADIUS_METERS},{latitude},{longitude});
     relation["railway"="station"](around:{TRAIN_RADIUS_METERS},{latitude},{longitude}););
    out center tags;'''
    return _normalize_elements(await _query_overpass(query), latitude, longitude, "train_station")


async def discover_gateway_train_stations(
    latitude: float, longitude: float
) -> list[dict[str, Any]]:
    """Find nearby Hauptbahnhof-style stations for car access when a city has none."""
    query = f'''[out:json][timeout:25];
    (node["railway"="station"]["name"~"Hbf|Hauptbahnhof",i](around:{GATEWAY_TRAIN_RADIUS_METERS},{latitude},{longitude});
     way["railway"="station"]["name"~"Hbf|Hauptbahnhof",i](around:{GATEWAY_TRAIN_RADIUS_METERS},{latitude},{longitude});
     relation["railway"="station"]["name"~"Hbf|Hauptbahnhof",i](around:{GATEWAY_TRAIN_RADIUS_METERS},{latitude},{longitude}););
    out center tags;'''
    return _normalize_elements(
        await _query_overpass(query), latitude, longitude, "train_station"
    )


def select_city_train_station(stations: list[dict[str, Any]], city_name: str) -> list[dict[str, Any]]:
    """Return the city's Hbf, otherwise its exact-name station, otherwise none."""
    selection = explain_train_station_filter(stations, city_name)
    return selection["selected"]


def explain_train_station_filter(
    stations: list[dict[str, Any]], city_name: str
) -> dict[str, Any]:
    """Expose the station-filter decision for development and manual verification."""
    normalized_city = _normalize_text(city_name)
    if not normalized_city:
        return {"rule_applied": "no_city_name", "selected": [], "candidates": []}
    city_words = normalized_city.split()
    hbf_stations = []
    exact_name_stations = []
    candidates = []
    for station in stations:
        normalized_name = _normalize_text(station["name"])
        name_words = normalized_name.split()
        is_city_hbf = all(word in name_words for word in city_words) and (
            "hbf" in name_words or "hauptbahnhof" in name_words
        )
        is_exact_city_name = normalized_name == normalized_city
        if is_city_hbf:
            hbf_stations.append(station)
            match_type = "city_hbf"
        elif is_exact_city_name:
            exact_name_stations.append(station)
            match_type = "city_exact_name"
        else:
            match_type = "not_eligible"
        candidates.append({
            "id": station["id"],
            "name": station["name"],
            "distance_km": station["distance_km"],
            "match_type": match_type,
        })
    if hbf_stations:
        selected = [min(hbf_stations, key=lambda item: item["distance_km"])]
        rule_applied = "city_hbf"
    elif exact_name_stations:
        selected = [min(exact_name_stations, key=lambda item: item["distance_km"])]
        rule_applied = "city_exact_name"
    else:
        selected = []
        rule_applied = "no_city_station_found"

    selected_ids = {station["id"] for station in selected}
    for candidate in candidates:
        candidate["selected"] = candidate["id"] in selected_ids
    return {"rule_applied": rule_applied, "selected": selected, "candidates": candidates}


async def discover_airports(latitude: float, longitude: float) -> list[dict[str, Any]]:
    query = f'''[out:json][timeout:25];
    (node["aeroway"="aerodrome"](around:{AIRPORT_RADIUS_METERS},{latitude},{longitude});
     way["aeroway"="aerodrome"](around:{AIRPORT_RADIUS_METERS},{latitude},{longitude});
     relation["aeroway"="aerodrome"](around:{AIRPORT_RADIUS_METERS},{latitude},{longitude}););
    out center tags;'''
    return _normalize_elements(await _query_overpass(query), latitude, longitude, "airport")


def _airport_has_passenger_service(airport: dict[str, Any]) -> bool:
    """Check whether an airport has an OpenStreetMap signal of passenger service."""
    return bool(
        airport["iata"]
        or airport["scheduled_service"] == "yes"
        or airport["passenger"] == "yes"
    )


def _airport_matches_city(airport: dict[str, Any], city_name: str) -> bool:
    """Check whether every normalized city word appears in the airport name."""
    city_words = _normalize_text(city_name).split()
    airport_words = _normalize_text(airport["name"]).split()
    return bool(city_words) and all(word in airport_words for word in city_words)


def select_city_airports(
    airports: list[dict[str, Any]], city_name: str
) -> list[dict[str, Any]]:
    """Keep commercial airports whose names identify them as airports of this city."""
    return [
        airport
        for airport in airports
        if _airport_has_passenger_service(airport)
        and _airport_matches_city(airport, city_name)
    ]


def explain_airport_filter(
    airports: list[dict[str, Any]], city_name: str
) -> list[dict[str, Any]]:
    """Expose airport eligibility decisions for development and manual verification."""
    candidates = []
    for airport in airports:
        reasons = []
        if airport["iata"]:
            reasons.append("iata_code")
        if airport["scheduled_service"] == "yes":
            reasons.append("scheduled_service")
        if airport["passenger"] == "yes":
            reasons.append("passenger_service")
        matches_city = _airport_matches_city(airport, city_name)
        candidates.append({
            "id": airport["id"],
            "name": airport["name"],
            "distance_km": airport["distance_km"],
            "matches_city": matches_city,
            "accepted": bool(reasons) and matches_city,
            "acceptance_reasons": reasons,
        })
    return candidates
