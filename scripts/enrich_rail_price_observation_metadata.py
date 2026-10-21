"""Classify the initial owner-provided rail prices for statistical estimates.

The current distances are documented route-band approximations, used only
until an imported European timetable supplies authoritative itinerary
distances.  Unknown observations remain untouched rather than guessed.
"""

from pathlib import Path
import sys

from sqlalchemy import select


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backend.database.models import RailPriceObservationRecord
from backend.database.session import SessionLocal, initialize_database


ROUTE_METADATA = {
    ("Berlin Hbf", "Kiel Hbf"): ("DE", "long_distance", 355.0, "any"),
    ("Berlin Hbf", "Hamburg Hbf"): ("DE", "long_distance", 255.0, "any"),
    ("Berlin Hbf", "Düsseldorf Hbf"): ("DE", "long_distance", 560.0, "any"),
    ("Stuttgart Hbf", "Hamburg Hbf"): ("DE", "long_distance", 650.0, "any"),
    ("Berlin Hbf", "Potsdam Hbf"): ("DE", "regional", 30.0, "any"),
    ("Berlin Hbf", "Leipzig Hbf"): ("DE", "long_distance", 190.0, "any"),
    ("Frankfurt (Main) Hbf", "Köln Hbf"): ("DE", "long_distance", 180.0, "any"),
    ("München Hbf", "Augsburg Hbf"): ("DE", "long_distance", 62.0, "any"),
    ("München Hbf", "Salzburg Hbf"): ("DE-AT", "international", 145.0, "any"),
    ("München Hbf", "Paris Est"): ("DE-FR", "international", 830.0, "db"),
    ("München Hbf", "Wien Hbf"): ("DE-AT", "international", 440.0, "any"),
    ("München Hbf", "Zürich HB"): ("DE-CH", "international", 315.0, None),
    ("München Hbf", "Amsterdam Centraal"): ("DE-NL", "international", 820.0, "db"),
    ("München Hbf", "Milano Centrale"): ("DE-IT", "international", 520.0, "db"),
    ("München Hbf", "Roma Termini"): ("DE-IT", "night", 930.0, "oebb"),
    ("München Hbf", "Zagreb Glavni kolodvor"): ("DE-HR", "international", 550.0, "any"),
    ("Berlin Hbf", "Zagreb Glavni kolodvor"): ("DE-HR", "international", 1050.0, "any"),
    ("Köln Hbf", "Zagreb Glavni kolodvor"): ("DE-HR", "international", 1100.0, "any"),
    ("Stuttgart Hbf", "Zagreb Glavni kolodvor"): ("DE-HR", "international", 800.0, "any"),
    ("Berlin Hbf", "Paris Est"): ("DE-FR", "international", 1050.0, "any"),
    ("Köln Hbf", "Paris Est"): ("DE-FR", "international", 500.0, "any"),
    ("Stuttgart Hbf", "Paris Est"): ("DE-FR", "international", 620.0, "any"),
    ("Berlin Hbf", "Wien Hbf"): ("DE-AT", "international", 680.0, "any"),
    ("Köln Hbf", "Wien Hbf"): ("DE-AT", "international", 900.0, "any"),
    ("Stuttgart Hbf", "Wien Hbf"): ("DE-AT", "international", 750.0, "any"),
}


def _provider_from_observation(record: RailPriceObservationRecord, default: str | None) -> str:
    """Prefer a stated DB or ÖBB seller over a route's generic default."""
    stated = (record.provider or "").casefold()
    if stated == "db":
        return "db"
    if stated in {"öbb", "oebb"}:
        return "oebb"
    return default or "any"


def enrich_observations() -> int:
    """Fill only missing classification metadata and return the updated count."""
    initialize_database()
    updated = 0
    with SessionLocal() as session:
        records = session.scalars(
            select(RailPriceObservationRecord).where(
                RailPriceObservationRecord.is_example.is_(False)
            )
        ).all()
        for record in records:
            metadata = ROUTE_METADATA.get((record.origin_station_name, record.destination_station_name))
            if metadata is None:
                continue
            market, service, distance_km, provider = metadata
            if record.origin_station_name == "München Hbf" and record.destination_station_name == "Salzburg Hbf":
                if record.train_types == ["RE"]:
                    market, service, distance_km, provider = "DE-AT", "regional", 145.0, "any"
                else:
                    market, service, distance_km, provider = "DE-AT", "international", 145.0, "any"
            record.market = market
            record.service_category = service
            record.provider_context = _provider_from_observation(record, provider)
            record.estimated_distance_km = distance_km
            updated += 1
        session.commit()
    return updated


if __name__ == "__main__":
    print(f"Classified {enrich_observations()} rail-price observation(s).")
