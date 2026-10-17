"""Application configuration and route registration for the TRO backend."""

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from backend.api.routes.health import router as health_router
from backend.api.routes.search import router as search_router
from backend.database.session import initialize_database


@asynccontextmanager
async def lifespan(_: FastAPI):
    """Initialize persistent storage before the API begins serving requests."""
    initialize_database()
    yield


app = FastAPI(
    title="Travel Route Optimizer API",
    version="0.1.0",
    description=(
        "The Travel Route Optimizer API discovers city transport nodes and "
        "assembles a multimodal route graph. It combines estimated car edges, "
        "regional train schedules, published flight schedules, and clearly "
        "labelled flight-price estimates where data is available."
    ),
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5500"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(health_router)
app.include_router(search_router)
