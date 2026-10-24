"""Expose read-only diagnostics for the modular railway-network registry."""

import os

from fastapi import APIRouter, HTTPException
from sqlalchemy import select

from backend.database.models import (
    RailNetworkImportRecord,
    RailStationInterchangeRecord,
    RailStationMembershipRecord,
    RailStationRecord,
)
from backend.database.session import SessionLocal


router = APIRouter(prefix="/rail-networks", tags=["Rail network registry"])


@router.get(
    "/germany/status",
    summary="Inspect Germany's canonical railway-station registry",
    description=(
        "Shows the offline GTFS import behind the new modular German rail mesh. "
        "It does not call an external provider and does not calculate a journey."
    ),
)
async def germany_registry_status() -> dict:
    """Return audit counts so the data build can be verified independently."""
    if os.getenv("APP_ENV", "development").casefold() == "production":
        raise HTTPException(status_code=404, detail="Not found")
    with SessionLocal() as session:
        imports = session.scalars(select(RailNetworkImportRecord)).all()
        return {
            "station_count": len(session.scalars(select(RailStationRecord)).all()),
            "membership_count": len(session.scalars(select(RailStationMembershipRecord)).all()),
            "regional_to_long_distance_interchange_count": len(session.scalars(
                select(RailStationInterchangeRecord).where(
                    RailStationInterchangeRecord.from_network_key == "de-regional",
                    RailStationInterchangeRecord.to_network_key == "de-long-distance",
                )
            ).all()),
            "imports": [
                {
                    "network": item.network_key,
                    "parent_station_count": item.parent_station_count,
                    "imported_at": item.imported_at.isoformat(),
                    "archive_fingerprint": item.archive_fingerprint,
                }
                for item in imports
            ],
        }
