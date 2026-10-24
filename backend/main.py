"""Application configuration and route registration for the TRO backend."""

from contextlib import asynccontextmanager
import os

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from backend.api.routes.health import router as health_router
from backend.api.routes.rail_price import router as rail_price_router
from backend.api.routes.rail_network import router as rail_network_router
from backend.api.routes.search import router as search_router
from backend.database.session import initialize_database
from backend.security import SlidingWindowRateLimiter


def _allowed_origins() -> list[str]:
    """Read explicit browser origins without accidentally enabling every site."""
    configured = os.getenv("CORS_ALLOWED_ORIGINS", "http://localhost:5500")
    return [origin.strip() for origin in configured.split(",") if origin.strip()]


def _positive_environment_integer(name: str, default: int) -> int:
    """Read a bounded integer setting while keeping a safe fallback on typos."""
    try:
        return max(1, int(os.getenv(name, str(default))))
    except ValueError:
        return default


search_rate_limiter = SlidingWindowRateLimiter(
    _positive_environment_integer("SEARCH_RATE_LIMIT_PER_MINUTE", 20)
)


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
    allow_origins=_allowed_origins(),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def protect_expensive_searches(request, call_next):
    """Reject oversized or excessive route searches before provider calls begin."""
    if request.method == "POST" and request.url.path == "/search":
        content_length = request.headers.get("content-length")
        if content_length and content_length.isdigit() and int(content_length) > 16_384:
            return JSONResponse(status_code=413, content={"detail": "Search request body is too large."})
        client_key = request.client.host if request.client else "unknown-client"
        if not search_rate_limiter.allowed(client_key):
            return JSONResponse(
                status_code=429,
                content={"detail": "Too many route searches. Please try again in a minute."},
                headers={"Retry-After": "60"},
            )
    return await call_next(request)

app.include_router(health_router)
app.include_router(rail_price_router)
app.include_router(rail_network_router)
app.include_router(search_router)
