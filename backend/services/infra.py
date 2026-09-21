"""Discovery, normalization, and selection of transport infrastructure."""

from __future__ import annotations

import math
import re
import unicodedata
from typing import Any

import httpx

OVERPASS_URL = "https://overpass-api.de/api/interpreter"
TRAIN_RADIUS_METERS = 20_000
AIRPORT_RADIUS_METERS = 50_000


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
    headers = {"User-Agent": "TRO-Travel-Route-Optimizer"}
    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.post(OVERPASS_URL, data={"data": query}, headers=headers)
        response.raise_for_status()
    return response.json().get("elements", [])


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
    query = f'''[out:json];
    (node["railway"="station"](around:{TRAIN_RADIUS_METERS},{latitude},{longitude});
     way["railway"="station"](around:{TRAIN_RADIUS_METERS},{latitude},{longitude});
     relation["railway"="station"](around:{TRAIN_RADIUS_METERS},{latitude},{longitude}););
    out center tags;'''
    return _normalize_elements(await _query_overpass(query), latitude, longitude, "train_station")


def select_city_train_station(stations: list[dict[str, Any]], city_name: str) -> list[dict[str, Any]]:
    """Return the city's Hbf, otherwise its exact-name station, otherwise none."""
    normalized_city = _normalize_text(city_name)
    if not normalized_city:
        return []
    city_words = normalized_city.split()
    hbf_stations = []
    exact_name_stations = []
    for station in stations:
        normalized_name = _normalize_text(station["name"])
        name_words = normalized_name.split()
        if all(word in name_words for word in city_words) and ("hbf" in name_words or "hauptbahnhof" in name_words):
            hbf_stations.append(station)
        elif normalized_name == normalized_city:
            exact_name_stations.append(station)
    if hbf_stations:
        return [min(hbf_stations, key=lambda item: item["distance_km"])]
    if exact_name_stations:
        return [min(exact_name_stations, key=lambda item: item["distance_km"])]
    return []


async def discover_airports(latitude: float, longitude: float) -> list[dict[str, Any]]:
    query = f'''[out:json];
    (node["aeroway"="aerodrome"](around:{AIRPORT_RADIUS_METERS},{latitude},{longitude});
     way["aeroway"="aerodrome"](around:{AIRPORT_RADIUS_METERS},{latitude},{longitude});
     relation["aeroway"="aerodrome"](around:{AIRPORT_RADIUS_METERS},{latitude},{longitude}););
    out center tags;'''
    airports = _normalize_elements(await _query_overpass(query), latitude, longitude, "airport")
    return [airport for airport in airports if airport["iata"] or airport["scheduled_service"] == "yes" or airport["passenger"] == "yes"]
