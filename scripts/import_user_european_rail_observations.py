"""Import European rail-price calibration observations supplied by the project owner.

The entries are route-level observations from Omio research.  They are not
scraped, not live offers, and do not create graph edges.  Each price belongs
to the complete journey shown to the researcher, including stated transfers.
"""

from datetime import UTC, datetime
from pathlib import Path
import sys

from sqlalchemy import select


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backend.database.models import RailPriceObservationRecord
from backend.database.session import Base, SessionLocal, engine, initialize_database


EUROPEAN_OBSERVATIONS = (
    ("München Hbf", "Paris Est", "2026-10-28", 7, 144.0, "DB", ["ICE", "TGV"], "Mannheim Hbf", "DB itinerary; one change in Mannheim."),
    ("München Hbf", "Paris Est", "2026-11-04", 14, 60.0, "DB", ["ICE", "TGV"], "Mannheim Hbf", "DB itinerary; one change in Mannheim."),
    ("München Hbf", "Wien Hbf", "2026-10-28", 7, 92.0, "DB/ÖBB", ["ICE", "RJ", "EC"], None, "DB or ÖBB itinerary; transfer details not stated."),
    ("München Hbf", "Wien Hbf", "2026-11-04", 14, 30.0, "DB/ÖBB", ["ICE", "RJ", "EC"], None, "DB or ÖBB itinerary; transfer details not stated."),
    ("München Hbf", "Zürich HB", "2026-10-28", 7, 100.0, "DB", ["ICE", "EC"], None, "DB itinerary; transfer details not stated."),
    ("München Hbf", "Zürich HB", "2026-11-04", 14, 50.0, "DB", ["ICE", "EC"], None, "DB itinerary; transfer details not stated."),
    ("München Hbf", "Zürich HB", "2026-10-28", 7, 129.0, "ÖBB", ["RJ", "EC"], None, "ÖBB itinerary; transfer details not stated."),
    ("München Hbf", "Zürich HB", "2026-11-04", 14, 125.0, "ÖBB", ["RJ", "EC"], None, "ÖBB itinerary; transfer details not stated."),
    ("München Hbf", "Amsterdam Centraal", "2026-10-28", 7, 180.0, "DB", ["ICE", "IC"], None, "DB itinerary; transfer details not stated."),
    ("München Hbf", "Amsterdam Centraal", "2026-11-04", 14, 88.0, "DB", ["ICE", "IC"], None, "DB itinerary; transfer details not stated."),
    ("München Hbf", "Milano Centrale", "2026-10-28", 7, 150.0, "DB", ["EC", "Frecciarossa", "Regionale"], "Verona Porta Nuova", "DB itinerary; stated change in Verona."),
    ("München Hbf", "Milano Centrale", "2026-11-04", 14, 60.0, "DB", ["EC", "Frecciarossa", "Regionale"], "Verona Porta Nuova", "DB itinerary; stated change in Verona."),
    ("München Hbf", "Roma Termini", "2026-10-28", 7, 80.0, "ÖBB", ["Nightjet"], None, "ÖBB Nightjet itinerary; transfer details not stated."),
    ("München Hbf", "Roma Termini", "2026-11-04", 14, 40.0, "ÖBB", ["Nightjet"], None, "ÖBB Nightjet itinerary; transfer details not stated."),
    ("München Hbf", "Zagreb Glavni kolodvor", "2026-10-28", 7, 95.0, "DB/ÖBB", ["EC"], "Salzburg Hbf", "DB or DB/ÖBB EC itinerary; stated change in Salzburg."),
    ("München Hbf", "Zagreb Glavni kolodvor", "2026-11-04", 14, 40.0, "DB/ÖBB", ["EC"], "Salzburg Hbf", "DB or DB/ÖBB EC itinerary; stated change in Salzburg."),
)

# These hub observations intentionally retain unknown provider, train and
# transfer fields instead of inventing details not present in the research.
EUROPEAN_HUB_OBSERVATIONS = (
    ("Berlin Hbf", "Zagreb Glavni kolodvor", "2026-10-22", 1, 180.0, "not stated", [], None, "Omio itinerary; operator and transfer details not stated."),
    ("Berlin Hbf", "Zagreb Glavni kolodvor", "2026-11-04", 14, 100.0, "not stated", [], None, "Omio itinerary; operator and transfer details not stated."),
    ("Köln Hbf", "Zagreb Glavni kolodvor", "2026-10-22", 1, 170.0, "not stated", [], None, "Omio itinerary; operator and transfer details not stated."),
    ("Köln Hbf", "Zagreb Glavni kolodvor", "2026-11-04", 14, 66.0, "not stated", [], None, "Omio itinerary; operator and transfer details not stated."),
    ("Stuttgart Hbf", "Zagreb Glavni kolodvor", "2026-10-22", 1, 150.0, "not stated", [], None, "Omio itinerary; operator and transfer details not stated."),
    ("Stuttgart Hbf", "Zagreb Glavni kolodvor", "2026-11-04", 14, 66.0, "not stated", [], None, "Omio itinerary; operator and transfer details not stated."),
    ("Berlin Hbf", "Paris Est", "2026-10-22", 1, 200.0, "not stated", [], None, "Omio itinerary; operator and transfer details not stated."),
    ("Berlin Hbf", "Paris Est", "2026-11-04", 14, 130.0, "not stated", [], None, "Omio itinerary; operator and transfer details not stated."),
    ("Köln Hbf", "Paris Est", "2026-10-22", 1, 185.0, "not stated", [], None, "Omio itinerary; operator and transfer details not stated."),
    ("Köln Hbf", "Paris Est", "2026-11-04", 14, 92.0, "not stated", [], None, "Omio itinerary; operator and transfer details not stated."),
    ("Stuttgart Hbf", "Paris Est", "2026-10-22", 1, 170.0, "not stated", [], None, "Omio itinerary; operator and transfer details not stated."),
    ("Stuttgart Hbf", "Paris Est", "2026-11-04", 14, 80.0, "not stated", [], None, "Omio itinerary; operator and transfer details not stated."),
    ("Berlin Hbf", "Wien Hbf", "2026-10-22", 1, 160.0, "not stated", [], None, "Omio itinerary; operator and transfer details not stated."),
    ("Berlin Hbf", "Wien Hbf", "2026-11-04", 14, 100.0, "not stated", [], None, "Omio itinerary; operator and transfer details not stated."),
    ("Köln Hbf", "Wien Hbf", "2026-10-22", 1, 160.0, "not stated", [], None, "Omio itinerary; operator and transfer details not stated."),
    ("Köln Hbf", "Wien Hbf", "2026-11-04", 14, 60.0, "not stated", [], None, "Omio itinerary; operator and transfer details not stated."),
    ("Stuttgart Hbf", "Wien Hbf", "2026-10-22", 1, 130.0, "not stated", [], None, "Omio itinerary; operator and transfer details not stated."),
    ("Stuttgart Hbf", "Wien Hbf", "2026-11-04", 14, 60.0, "not stated", [], None, "Omio itinerary; operator and transfer details not stated."),
)


def import_observations() -> int:
    """Store the supplied European route prices once and return their count."""
    initialize_database()
    Base.metadata.create_all(bind=engine, tables=[RailPriceObservationRecord.__table__])
    added = 0
    with SessionLocal() as session:
        for origin, destination, travel_date, days, price, provider, train_types, transfer, summary in (
            *EUROPEAN_OBSERVATIONS,
            *EUROPEAN_HUB_OBSERVATIONS,
        ):
            existing = session.scalar(
                select(RailPriceObservationRecord).where(
                    RailPriceObservationRecord.origin_station_name == origin,
                    RailPriceObservationRecord.destination_station_name == destination,
                    RailPriceObservationRecord.travel_date == travel_date,
                    RailPriceObservationRecord.price_eur == price,
                    RailPriceObservationRecord.provider == provider,
                    RailPriceObservationRecord.observation_source == "user_provided_omio_calibration",
                )
            )
            if existing is not None:
                continue
            session.add(RailPriceObservationRecord(
                origin_station_name=origin,
                origin_station_id=None,
                destination_station_name=destination,
                destination_station_id=None,
                travel_date=travel_date,
                departure_time=None,
                arrival_time=None,
                search_date="2026-10-21",
                booking_days_ahead=days,
                passengers=1,
                travel_class="2",
                discount_profile="not_stated",
                has_deutschlandticket=False,
                fare_type="user_reported_omio_itinerary",
                price_eur=price,
                price_min_eur=None,
                price_max_eur=None,
                currency="EUR",
                provider=provider,
                provider_url=None,
                train_types=train_types,
                itinerary_summary=summary,
                transfer_count=1 if transfer else None,
                duration_minutes=None,
                is_refundable=None,
                observation_source="user_provided_omio_calibration",
                observed_at=datetime.now(UTC).replace(tzinfo=None),
                is_example=False,
                notes=(
                    "Entered from project-owner Omio research on 2026-10-21. "
                    "Travel dates are inferred from 'this week' and 'two weeks'. "
                    f"Known transfer: {transfer or 'not stated'}. "
                    "The price applies to the complete displayed itinerary."
                ),
            ))
            added += 1
        session.commit()
    return added


if __name__ == "__main__":
    print(f"Added {import_observations()} European rail calibration observation(s).")
