import httpx


async def discover_infrastructure(latitude: float, longitude: float):

    url = "https://overpass-api.de/api/interpreter"

    query = f"""
    [out:json];

    (
        node["railway"="station"](around:20000,{latitude},{longitude});
        node["aeroway"="aerodrome"](around:20000,{latitude},{longitude});
    );

    out;
    """

    headers = {
        "User-Agent": "TRO-Travel-Route-Optimizer"
    }

    async with httpx.AsyncClient() as client:

        response = await client.post(
            url,
            data={"data": query},
            headers=headers
        )

    data = response.json()

    return data