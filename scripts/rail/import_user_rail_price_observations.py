"""Import rail-price observations supplied by the TRO project owner.

This intentionally small, idempotent import records calibration evidence in
the local database.  It does not query a ticket website, create train graph
edges, or claim that any recorded price is currently available.
"""

from datetime import UTC, datetime
from pathlib import Path
import sys

from sqlalchemy import select


PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backend.database.models import RailPriceObservationRecord
from backend.database.session import Base, SessionLocal, engine, initialize_database


USER_RAIL_OBSERVATIONS = (
    {
        "origin_station_name": "Berlin Hbf",
        "destination_station_name": "Kiel Hbf",
        "travel_date": "2026-11-04",
        "booking_days_ahead": 14,
        "price_eur": 60.0,
        "price_min_eur": None,
        "price_max_eur": None,
        "itinerary_summary": "ICE; user-reported price about two weeks before travel.",
    },
    {
        "origin_station_name": "Berlin Hbf",
        "destination_station_name": "Hamburg Hbf",
        "travel_date": "2026-11-04",
        "booking_days_ahead": 14,
        "price_eur": 50.0,
        "price_min_eur": None,
        "price_max_eur": None,
        "itinerary_summary": "ICE; user-reported price about two weeks before travel.",
    },
    {
        "origin_station_name": "Berlin Hbf",
        "destination_station_name": "Hamburg Hbf",
        "travel_date": "2026-10-22",
        "booking_days_ahead": 1,
        "price_eur": 90.0,
        "price_min_eur": None,
        "price_max_eur": None,
        "itinerary_summary": "ICE; user-reported price one day before travel.",
    },
    {
        "origin_station_name": "Berlin Hbf",
        "destination_station_name": "Düsseldorf Hbf",
        "travel_date": "2026-10-22",
        "booking_days_ahead": 1,
        "price_eur": 130.0,
        "price_min_eur": None,
        "price_max_eur": None,
        "itinerary_summary": "ICE; user-reported price one day before travel.",
    },
    {
        "origin_station_name": "Berlin Hbf",
        "destination_station_name": "Hamburg Hbf",
        "travel_date": "2026-11-04",
        "booking_days_ahead": 14,
        "price_eur": 70.0,
        "price_min_eur": None,
        "price_max_eur": None,
        "itinerary_summary": "ICE; second user-reported price about two weeks before travel.",
    },
    {
        "origin_station_name": "Stuttgart Hbf",
        "destination_station_name": "Hamburg Hbf",
        "travel_date": "2026-11-04",
        "booking_days_ahead": 14,
        "price_eur": 45.0,
        "price_min_eur": 20.0,
        "price_max_eur": 70.0,
        "itinerary_summary": "ICE; user-reported price range about two weeks before travel.",
    },
    {
        "origin_station_name": "Stuttgart Hbf",
        "destination_station_name": "Hamburg Hbf",
        "travel_date": "2026-10-22",
        "booking_days_ahead": 1,
        "price_eur": 100.0,
        "price_min_eur": 70.0,
        "price_max_eur": 130.0,
        "itinerary_summary": "ICE; user-reported price range one day before travel.",
    },
    {
        "origin_station_name": "Berlin Hbf",
        "destination_station_name": "Potsdam Hbf",
        "travel_date": "2026-10-22",
        "booking_days_ahead": 1,
        "price_eur": 5.0,
        "price_min_eur": None,
        "price_max_eur": None,
        "itinerary_summary": "S7; cash fare reported by user. A valid Deutschlandticket covers the marginal fare.",
    },
    {
        "origin_station_name": "Berlin Hbf",
        "destination_station_name": "Leipzig Hbf",
        "travel_date": "2026-10-22",
        "booking_days_ahead": 1,
        "price_eur": 60.0,
        "price_min_eur": None,
        "price_max_eur": None,
        "itinerary_summary": "ICE; user-reported price one day before travel.",
    },
    {
        "origin_station_name": "Berlin Hbf",
        "destination_station_name": "Leipzig Hbf",
        "travel_date": "2026-11-04",
        "booking_days_ahead": 14,
        "price_eur": 30.0,
        "price_min_eur": None,
        "price_max_eur": None,
        "itinerary_summary": "ICE; user-reported price about two weeks before travel.",
    },
    {
        "origin_station_name": "Frankfurt (Main) Hbf",
        "destination_station_name": "Köln Hbf",
        "travel_date": "2026-11-04",
        "booking_days_ahead": 14,
        "price_eur": 35.0,
        "price_min_eur": None,
        "price_max_eur": None,
        "itinerary_summary": "ICE; user-reported price about two weeks before travel.",
    },
    {
        "origin_station_name": "Frankfurt (Main) Hbf",
        "destination_station_name": "Köln Hbf",
        "travel_date": "2026-10-22",
        "booking_days_ahead": 1,
        "price_eur": 95.0,
        "price_min_eur": None,
        "price_max_eur": None,
        "itinerary_summary": "ICE; user-reported price one day before travel.",
    },
    {
        "origin_station_name": "München Hbf",
        "destination_station_name": "Augsburg Hbf",
        "travel_date": "2026-10-22",
        "booking_days_ahead": 1,
        "price_eur": 30.0,
        "price_min_eur": None,
        "price_max_eur": None,
        "itinerary_summary": "ICE; user-reported price one day before travel.",
    },
    {
        "origin_station_name": "München Hbf",
        "destination_station_name": "Augsburg Hbf",
        "travel_date": "2026-11-04",
        "booking_days_ahead": 14,
        "price_eur": 17.0,
        "price_min_eur": None,
        "price_max_eur": None,
        "itinerary_summary": "ICE; user-reported price about two weeks before travel.",
    },
    {
        "origin_station_name": "München Hbf",
        "destination_station_name": "Salzburg Hbf",
        "travel_date": "2026-11-04",
        "booking_days_ahead": 14,
        "price_eur": 17.0,
        "price_min_eur": None,
        "price_max_eur": None,
        "itinerary_summary": "RJ/EC; user-reported international price about two weeks before travel.",
    },
    {
        "origin_station_name": "München Hbf",
        "destination_station_name": "Salzburg Hbf",
        "travel_date": "2026-11-04",
        "booking_days_ahead": 14,
        "price_eur": 30.0,
        "price_min_eur": None,
        "price_max_eur": None,
        "itinerary_summary": "RE; user-reported international regional price about two weeks before travel.",
    },
    {
        "origin_station_name": "München Hbf",
        "destination_station_name": "Salzburg Hbf",
        "travel_date": "2026-10-22",
        "booking_days_ahead": 1,
        "price_eur": 22.0,
        "price_min_eur": None,
        "price_max_eur": None,
        "itinerary_summary": "RJ/EC; user-reported international price one day before travel.",
    },
    {
        "origin_station_name": "München Hbf",
        "destination_station_name": "Salzburg Hbf",
        "travel_date": "2026-10-22",
        "booking_days_ahead": 1,
        "price_eur": 30.0,
        "price_min_eur": None,
        "price_max_eur": None,
        "itinerary_summary": "RE; user-reported international regional price one day before travel.",
    },
)


def import_observations() -> int:
    """Store each supplied observation once and return the insertion count."""
    # Apply TRO's small SQLite migrations before inserting range columns.
    initialize_database()
    Base.metadata.create_all(bind=engine, tables=[RailPriceObservationRecord.__table__])
    added = 0
    with SessionLocal() as session:
        for observation in USER_RAIL_OBSERVATIONS:
            existing = session.scalar(
                select(RailPriceObservationRecord).where(
                    RailPriceObservationRecord.origin_station_name == observation["origin_station_name"],
                    RailPriceObservationRecord.destination_station_name == observation["destination_station_name"],
                    RailPriceObservationRecord.travel_date == observation["travel_date"],
                    RailPriceObservationRecord.price_eur == observation["price_eur"],
                    RailPriceObservationRecord.observation_source == "user_provided_calibration",
                )
            )
            if existing is not None:
                continue
            session.add(
                RailPriceObservationRecord(
                    **observation,
                    origin_station_id=None,
                    destination_station_id=None,
                    departure_time=None,
                    arrival_time=None,
                    search_date="2026-10-21",
                    passengers=1,
                    travel_class="2",
                    discount_profile="not_stated",
                    has_deutschlandticket=False,
                    fare_type="user_reported_estimate",
                    currency="EUR",
                    provider="user-provided calibration",
                    provider_url=None,
                    train_types=(
                        ["S-Bahn"] if observation["destination_station_name"] == "Potsdam Hbf"
                        else ["RJ", "EC"] if "RJ/EC" in observation["itinerary_summary"]
                        else ["RE"] if observation["itinerary_summary"].startswith("RE;")
                        else ["ICE"]
                    ),
                    transfer_count=None,
                    duration_minutes=None,
                    is_refundable=None,
                    observation_source="user_provided_calibration",
                    observed_at=datetime.now(UTC).replace(tzinfo=None),
                    is_example=False,
                    notes=(
                        "Entered from project-owner notes on 2026-10-21. "
                        "Travel dates are inferred from 'tomorrow' and 'two weeks'; "
                        "exact departure time, fare conditions and discount are not stated. "
                        "The Bayern-Ticket note is retained as context, not used as a general fare rule."
                    ),
                )
            )
            added += 1
        session.commit()
    return added


if __name__ == "__main__":
    print(f"Added {import_observations()} user-provided ICE calibration observation(s).")
