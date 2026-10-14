"""Availability route kept separate from application configuration."""

from fastapi import APIRouter

router = APIRouter(tags=["Health"])


@router.get("/", summary="Check API availability", response_description="Confirmation that the TRO API is running.")
def root():
    """Report that the backend service is available without calling external providers."""
    return {"message": "TRO API is running"}
