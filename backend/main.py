from fastapi import FastAPI
import httpx

app = FastAPI()


@app.get("/")
def root():
    return {"message": "TRO API is running"}


@app.get("/geocode")
async def geocode(city: str):

    url = "https://nominatim.openstreetmap.org/search"

    params = {
        "q": city,
        "format": "jsonv2",
        "limit": 1
    }

    headers = {
        "User-Agent": "TRO-Travel-Route-Optimizer"
    }

    async with httpx.AsyncClient() as client:

        response = await client.get(
            url,
            params=params,
            headers=headers
        )

    data = response.json()

    if not data:
        return {
            "error": "City not found"
        }

    result = data[0]

    return {
        "name": result["display_name"],
        "latitude": float(result["lat"]),
        "longitude": float(result["lon"])
    }