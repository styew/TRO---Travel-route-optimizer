from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from backend.services.geocoding import geocode

app = FastAPI()

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

    print("Origin:", request.origin)
    print("Destination:", request.destination)
    print("Date:", request.date)

    origin_data = await geocode(request.origin)
    destination_data = await geocode(request.destination)

    print("return after geo def:", origin_data,destination_data)

    return {
        "origin": origin_data,
        "destination": destination_data
    }
