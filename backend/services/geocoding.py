import httpx


async def geocode(city: str):

    url = "https://nominatim.openstreetmap.org/search"

    params = {
        "q": city,
        "format": "jsonv2",
        "limit": 1,
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

    if not data:
        return {
            "error": "City not found"
        }

    result = data[0]

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
