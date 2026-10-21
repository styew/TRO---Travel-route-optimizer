"""Create the manual rail-price table and add one clearly labelled example.

Run this script once before opening the database in DB Browser for SQLite.
It never deletes or modifies existing TRO cache, flight or infrastructure data.
"""

from datetime import UTC, datetime
from pathlib import Path
import sys

from sqlalchemy import select


# Allow this maintenance script to run directly from the project root on Windows.
PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backend.database.models import RailPriceObservationRecord
from backend.database.session import Base, SessionLocal, engine, initialize_database


def create_table_and_example() -> bool:
    """Create the table and insert the example row once, returning its status."""
    # Keep direct maintenance use aligned with the application's SQLite schema.
    initialize_database()
    Base.metadata.create_all(bind=engine, tables=[RailPriceObservationRecord.__table__])
    with SessionLocal() as session:
        example = session.scalar(
            select(RailPriceObservationRecord).where(
                RailPriceObservationRecord.is_example.is_(True),
                RailPriceObservationRecord.origin_station_name == "Nürnberg Hbf",
                RailPriceObservationRecord.destination_station_name == "Berlin Hbf",
            )
        )
        if example is not None:
            return False

        session.add(
            RailPriceObservationRecord(
                origin_station_name="Nürnberg Hbf",
                origin_station_id=None,
                destination_station_name="Berlin Hbf",
                destination_station_id=None,
                travel_date="2026-11-15",
                departure_time="08:00",
                arrival_time="11:42",
                search_date="2026-10-19",
                booking_days_ahead=27,
                passengers=1,
                travel_class="2",
                discount_profile="none",
                has_deutschlandticket=False,
                fare_type="lowest_available",
                price_eur=49.90,
                currency="EUR",
                provider="example only",
                provider_url=None,
                train_types=["ICE"],
                itinerary_summary="Example only: ICE journey Nürnberg Hbf → Berlin Hbf.",
                transfer_count=0,
                duration_minutes=222,
                is_refundable=None,
                observation_source="example",
                observed_at=datetime.now(UTC).replace(tzinfo=None),
                is_example=True,
                notes="This is a sample row, not a real fare. Delete it before analysing real prices.",
            )
        )
        session.commit()
    return True


if __name__ == "__main__":
    added = create_table_and_example()
    print("Created rail_price_observations and added its example row." if added else "Table and example row already exist.")
