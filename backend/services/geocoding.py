"""Resolve a traveller's city text to a city-level geographic anchor."""

import re
import unicodedata

import httpx


def _normalized_words(value: str) -> set[str]:
    """Create accent-insensitive tokens for comparing a query with a municipality."""
    decomposed = unicodedata.normalize("NFKD", value)
    ascii_value = "".join(char for char in decomposed if not unicodedata.combining(char))
    return set(re.sub(r"[^a-z0-9]+", " ", ascii_value.lower()).split())


def _select_city_result(query: str, results: list[dict]) -> dict | None:
    """Choose a matching municipality instead of an unrelated address or street.

    Nominatim can rank an address containing a similar string above a city, such
    as Roseheim Court in the United States for the query ``Rosenheim``. A valid
    city result must expose a municipality field whose words include the words
    requested by the traveller.
    """
    query_words = _normalized_words(query)
    if not query_words:
        return None
    for result in results:
        address = result.get("address", {})
        municipality = next(
            (address[key] for key in ("city", "town", "village", "municipality") if address.get(key)),
            "",
        )
        municipality_words = _normalized_words(municipality)
        if municipality_words and query_words <= municipality_words:
            return result
    return None


async def geocode(city: str):
    """Find a municipality matching the entered city name through Nominatim."""

    url = "https://nominatim.openstreetmap.org/search"

    params = {
        "q": city,
        "format": "jsonv2",
        "limit": 10,
        "addressdetails": 1,
    }

    headers = {
        "User-Agent": "TRO-Travel-Route-Optimizer"
    }

    async with httpx.AsyncClient(timeout=15.0) as client:
        response = await client.get(
            url,
            params=params,
            headers=headers
        )
        response.raise_for_status()

    data = response.json()

    result = _select_city_result(city, data)
    if result is None:
        return {
            "error": "City not found"
        }

    address = result.get("address", {})
    city_name = next(
        (
            address[key]
            for key in ("city", "town", "village", "municipality")
            if address.get(key)
        ),
        result.get("name", city),
    )

    return {
        "name": result["display_name"],
        "city_name": city_name,
        "latitude": float(result["lat"]),
        "longitude": float(result["lon"]),
        "source_id": f"nominatim:{result['osm_type']}:{result['osm_id']}",
        "source": "nominatim",
    }
