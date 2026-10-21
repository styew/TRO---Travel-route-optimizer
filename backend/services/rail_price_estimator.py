"""Estimate rail fares from transparent market profiles rather than live offers.

This module never claims a current ticket price.  It selects a documented
profile based on the service category, market, distance, and booking horizon,
then returns a low, typical, and high estimate for the whole itinerary.
"""

from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime
from statistics import median

from sqlalchemy import delete, or_, select

from backend.database.models import RailPriceObservationRecord, RailPriceProfileRecord
from backend.database.session import SessionLocal


@dataclass(frozen=True)
class RailPriceEstimate:
    """Represents an explainable fare range for one passenger's rail itinerary."""

    minimum_eur: float
    typical_eur: float
    maximum_eur: float
    confidence: str
    sample_size: int | None
    price_status: str
    profile_key: str | None
    source: str
    disclaimer: str

    def as_dict(self) -> dict:
        """Serialize the estimate for FastAPI responses and future graph metadata."""
        return {
            "estimated_min_eur": self.minimum_eur,
            "estimated_typical_eur": self.typical_eur,
            "estimated_max_eur": self.maximum_eur,
            "confidence": self.confidence,
            "sample_size": self.sample_size,
            "price_status": self.price_status,
            "profile_key": self.profile_key,
            "source": self.source,
            "disclaimer": self.disclaimer,
        }


PILOT_PROFILES = (
    {
        "profile_key": "de-hr-international-750-1200km-0-2d-any",
        "market": "DE-HR", "service_category": "international", "provider_context": "any",
        "distance_min_km": 750.0, "distance_max_km": 1200.0,
        "booking_days_min": 0, "booking_days_max": 2,
        "price_min_eur": 150.0, "price_typical_eur": 170.0, "price_max_eur": 180.0,
        "confidence": "low", "source": "TRO pilot calibration from user-provided Germany–Zagreb observations",
        "is_prototype": True,
        "notes": "One adult, fare conditions not stated. Berlin, Köln and Stuttgart observations for travel tomorrow.",
    },
    {
        "profile_key": "de-hr-international-750-1200km-3-21d-any",
        "market": "DE-HR", "service_category": "international", "provider_context": "any",
        "distance_min_km": 750.0, "distance_max_km": 1200.0,
        "booking_days_min": 3, "booking_days_max": 21,
        "price_min_eur": 66.0, "price_typical_eur": 77.0, "price_max_eur": 100.0,
        "confidence": "low", "source": "TRO pilot calibration from user-provided Germany–Zagreb observations",
        "is_prototype": True,
        "notes": "One adult, fare conditions not stated. Berlin, Köln and Stuttgart observations about two weeks ahead.",
    },
    {
        "profile_key": "de-fr-international-400-699km-0-2d-any",
        "market": "DE-FR", "service_category": "international", "provider_context": "any",
        "distance_min_km": 400.0, "distance_max_km": 699.0,
        "booking_days_min": 0, "booking_days_max": 2,
        "price_min_eur": 170.0, "price_typical_eur": 178.0, "price_max_eur": 185.0,
        "confidence": "low", "source": "TRO pilot calibration from user-provided Köln/Stuttgart–Paris observations",
        "is_prototype": True,
        "notes": "One adult, fare conditions not stated. Travel tomorrow.",
    },
    {
        "profile_key": "de-fr-international-400-699km-3-21d-any",
        "market": "DE-FR", "service_category": "international", "provider_context": "any",
        "distance_min_km": 400.0, "distance_max_km": 699.0,
        "booking_days_min": 3, "booking_days_max": 21,
        "price_min_eur": 80.0, "price_typical_eur": 86.0, "price_max_eur": 92.0,
        "confidence": "low", "source": "TRO pilot calibration from user-provided Köln/Stuttgart–Paris observations",
        "is_prototype": True,
        "notes": "One adult, fare conditions not stated. Travel about two weeks ahead.",
    },
    {
        "profile_key": "de-fr-international-1000-1200km-0-2d-any",
        "market": "DE-FR", "service_category": "international", "provider_context": "any",
        "distance_min_km": 1000.0, "distance_max_km": 1200.0,
        "booking_days_min": 0, "booking_days_max": 2,
        "price_min_eur": 200.0, "price_typical_eur": 200.0, "price_max_eur": 200.0,
        "confidence": "low", "source": "TRO pilot calibration from user-provided Berlin–Paris observation",
        "is_prototype": True,
        "notes": "Very narrow prototype: Berlin–Paris, fare conditions not stated, travel tomorrow.",
    },
    {
        "profile_key": "de-fr-international-1000-1200km-3-21d-any",
        "market": "DE-FR", "service_category": "international", "provider_context": "any",
        "distance_min_km": 1000.0, "distance_max_km": 1200.0,
        "booking_days_min": 3, "booking_days_max": 21,
        "price_min_eur": 130.0, "price_typical_eur": 130.0, "price_max_eur": 130.0,
        "confidence": "low", "source": "TRO pilot calibration from user-provided Berlin–Paris observation",
        "is_prototype": True,
        "notes": "Very narrow prototype: Berlin–Paris, fare conditions not stated, travel about two weeks ahead.",
    },
    {
        "profile_key": "de-at-international-600-799km-0-2d-any",
        "market": "DE-AT", "service_category": "international", "provider_context": "any",
        "distance_min_km": 600.0, "distance_max_km": 799.0,
        "booking_days_min": 0, "booking_days_max": 2,
        "price_min_eur": 130.0, "price_typical_eur": 145.0, "price_max_eur": 160.0,
        "confidence": "low", "source": "TRO pilot calibration from user-provided Berlin/Stuttgart–Wien observations",
        "is_prototype": True,
        "notes": "One adult, fare conditions not stated. Travel tomorrow.",
    },
    {
        "profile_key": "de-at-international-600-799km-3-21d-any",
        "market": "DE-AT", "service_category": "international", "provider_context": "any",
        "distance_min_km": 600.0, "distance_max_km": 799.0,
        "booking_days_min": 3, "booking_days_max": 21,
        "price_min_eur": 60.0, "price_typical_eur": 80.0, "price_max_eur": 100.0,
        "confidence": "low", "source": "TRO pilot calibration from user-provided Berlin/Stuttgart–Wien observations",
        "is_prototype": True,
        "notes": "One adult, fare conditions not stated. Travel about two weeks ahead.",
    },
    {
        "profile_key": "de-at-international-800-1000km-0-2d-any",
        "market": "DE-AT", "service_category": "international", "provider_context": "any",
        "distance_min_km": 800.0, "distance_max_km": 1000.0,
        "booking_days_min": 0, "booking_days_max": 2,
        "price_min_eur": 160.0, "price_typical_eur": 160.0, "price_max_eur": 160.0,
        "confidence": "low", "source": "TRO pilot calibration from user-provided Köln–Wien observation",
        "is_prototype": True,
        "notes": "Very narrow prototype: Köln–Wien, fare conditions not stated, travel tomorrow.",
    },
    {
        "profile_key": "de-at-international-800-1000km-3-21d-any",
        "market": "DE-AT", "service_category": "international", "provider_context": "any",
        "distance_min_km": 800.0, "distance_max_km": 1000.0,
        "booking_days_min": 3, "booking_days_max": 21,
        "price_min_eur": 60.0, "price_typical_eur": 60.0, "price_max_eur": 60.0,
        "confidence": "low", "source": "TRO pilot calibration from user-provided Köln–Wien observation",
        "is_prototype": True,
        "notes": "Very narrow prototype: Köln–Wien, fare conditions not stated, travel about two weeks ahead.",
    },
    {
        "profile_key": "de-nl-international-750-1000km-3-7d-db",
        "market": "DE-NL",
        "service_category": "international",
        "provider_context": "db",
        "distance_min_km": 750.0,
        "distance_max_km": 1000.0,
        "booking_days_min": 3,
        "booking_days_max": 7,
        "price_min_eur": 180.0,
        "price_typical_eur": 180.0,
        "price_max_eur": 180.0,
        "confidence": "low",
        "source": "TRO pilot calibration from user-provided München–Amsterdam DB observation",
        "is_prototype": True,
        "notes": "Very narrow prototype: München–Amsterdam DB itinerary, observed within one week.",
    },
    {
        "profile_key": "de-nl-international-750-1000km-8-21d-db",
        "market": "DE-NL",
        "service_category": "international",
        "provider_context": "db",
        "distance_min_km": 750.0,
        "distance_max_km": 1000.0,
        "booking_days_min": 8,
        "booking_days_max": 21,
        "price_min_eur": 88.0,
        "price_typical_eur": 88.0,
        "price_max_eur": 88.0,
        "confidence": "low",
        "source": "TRO pilot calibration from user-provided München–Amsterdam DB observation",
        "is_prototype": True,
        "notes": "Very narrow prototype: München–Amsterdam DB itinerary, observed about two weeks ahead.",
    },
    {
        "profile_key": "de-it-international-400-599km-3-7d-db",
        "market": "DE-IT",
        "service_category": "international",
        "provider_context": "db",
        "distance_min_km": 400.0,
        "distance_max_km": 599.0,
        "booking_days_min": 3,
        "booking_days_max": 7,
        "price_min_eur": 150.0,
        "price_typical_eur": 150.0,
        "price_max_eur": 150.0,
        "confidence": "low",
        "source": "TRO pilot calibration from user-provided München–Milano DB observation",
        "is_prototype": True,
        "notes": "Very narrow prototype: München–Milano DB itinerary via Verona, observed within one week.",
    },
    {
        "profile_key": "de-it-international-400-599km-8-21d-db",
        "market": "DE-IT",
        "service_category": "international",
        "provider_context": "db",
        "distance_min_km": 400.0,
        "distance_max_km": 599.0,
        "booking_days_min": 8,
        "booking_days_max": 21,
        "price_min_eur": 60.0,
        "price_typical_eur": 60.0,
        "price_max_eur": 60.0,
        "confidence": "low",
        "source": "TRO pilot calibration from user-provided München–Milano DB observation",
        "is_prototype": True,
        "notes": "Very narrow prototype: München–Milano DB itinerary via Verona, observed about two weeks ahead.",
    },
    {
        "profile_key": "de-it-night-750-1200km-3-7d-oebb",
        "market": "DE-IT",
        "service_category": "night",
        "provider_context": "oebb",
        "distance_min_km": 750.0,
        "distance_max_km": 1200.0,
        "booking_days_min": 3,
        "booking_days_max": 7,
        "price_min_eur": 80.0,
        "price_typical_eur": 80.0,
        "price_max_eur": 80.0,
        "confidence": "low",
        "source": "TRO pilot calibration from user-provided München–Roma ÖBB Nightjet observation",
        "is_prototype": True,
        "notes": "Very narrow prototype: München–Roma Nightjet itinerary, observed within one week. Berth type is not stated.",
    },
    {
        "profile_key": "de-it-night-750-1200km-8-21d-oebb",
        "market": "DE-IT",
        "service_category": "night",
        "provider_context": "oebb",
        "distance_min_km": 750.0,
        "distance_max_km": 1200.0,
        "booking_days_min": 8,
        "booking_days_max": 21,
        "price_min_eur": 40.0,
        "price_typical_eur": 40.0,
        "price_max_eur": 40.0,
        "confidence": "low",
        "source": "TRO pilot calibration from user-provided München–Roma ÖBB Nightjet observation",
        "is_prototype": True,
        "notes": "Very narrow prototype: München–Roma Nightjet itinerary, observed about two weeks ahead. Berth type is not stated.",
    },
    {
        "profile_key": "de-hr-international-400-599km-3-7d-any",
        "market": "DE-HR",
        "service_category": "international",
        "provider_context": "any",
        "distance_min_km": 400.0,
        "distance_max_km": 599.0,
        "booking_days_min": 3,
        "booking_days_max": 7,
        "price_min_eur": 95.0,
        "price_typical_eur": 95.0,
        "price_max_eur": 95.0,
        "confidence": "low",
        "source": "TRO pilot calibration from user-provided München–Zagreb DB/ÖBB observation",
        "is_prototype": True,
        "notes": "Very narrow prototype: München–Zagreb EC itinerary via Salzburg, observed within one week.",
    },
    {
        "profile_key": "de-hr-international-400-599km-8-21d-any",
        "market": "DE-HR",
        "service_category": "international",
        "provider_context": "any",
        "distance_min_km": 400.0,
        "distance_max_km": 599.0,
        "booking_days_min": 8,
        "booking_days_max": 21,
        "price_min_eur": 40.0,
        "price_typical_eur": 40.0,
        "price_max_eur": 40.0,
        "confidence": "low",
        "source": "TRO pilot calibration from user-provided München–Zagreb DB/ÖBB observation",
        "is_prototype": True,
        "notes": "Very narrow prototype: München–Zagreb EC itinerary via Salzburg, observed about two weeks ahead.",
    },
    {
        "profile_key": "de-fr-international-750-1000km-3-7d-db",
        "market": "DE-FR",
        "service_category": "international",
        "provider_context": "db",
        "distance_min_km": 750.0,
        "distance_max_km": 1000.0,
        "booking_days_min": 3,
        "booking_days_max": 7,
        "price_min_eur": 144.0,
        "price_typical_eur": 144.0,
        "price_max_eur": 144.0,
        "confidence": "low",
        "source": "TRO pilot calibration from user-provided München–Paris DB observation",
        "is_prototype": True,
        "notes": "Very narrow prototype: München–Paris DB itinerary with a Mannheim change, observed within one week.",
    },
    {
        "profile_key": "de-fr-international-750-1000km-8-21d-db",
        "market": "DE-FR",
        "service_category": "international",
        "provider_context": "db",
        "distance_min_km": 750.0,
        "distance_max_km": 1000.0,
        "booking_days_min": 8,
        "booking_days_max": 21,
        "price_min_eur": 60.0,
        "price_typical_eur": 60.0,
        "price_max_eur": 60.0,
        "confidence": "low",
        "source": "TRO pilot calibration from user-provided München–Paris DB observation",
        "is_prototype": True,
        "notes": "Very narrow prototype: München–Paris DB itinerary with a Mannheim change, observed about two weeks ahead.",
    },
    {
        "profile_key": "de-at-international-400-599km-3-7d-any",
        "market": "DE-AT",
        "service_category": "international",
        "provider_context": "any",
        "distance_min_km": 400.0,
        "distance_max_km": 599.0,
        "booking_days_min": 3,
        "booking_days_max": 7,
        "price_min_eur": 92.0,
        "price_typical_eur": 92.0,
        "price_max_eur": 92.0,
        "confidence": "low",
        "source": "TRO pilot calibration from user-provided München–Wien DB/ÖBB observation",
        "is_prototype": True,
        "notes": "Very narrow prototype: München–Wien international itinerary, observed within one week.",
    },
    {
        "profile_key": "de-at-international-400-599km-8-21d-any",
        "market": "DE-AT",
        "service_category": "international",
        "provider_context": "any",
        "distance_min_km": 400.0,
        "distance_max_km": 599.0,
        "booking_days_min": 8,
        "booking_days_max": 21,
        "price_min_eur": 30.0,
        "price_typical_eur": 30.0,
        "price_max_eur": 30.0,
        "confidence": "low",
        "source": "TRO pilot calibration from user-provided München–Wien DB/ÖBB observation",
        "is_prototype": True,
        "notes": "Very narrow prototype: München–Wien international itinerary, observed about two weeks ahead.",
    },
    {
        "profile_key": "de-ch-international-200-399km-3-7d-db",
        "market": "DE-CH",
        "service_category": "international",
        "provider_context": "db",
        "distance_min_km": 200.0,
        "distance_max_km": 399.0,
        "booking_days_min": 3,
        "booking_days_max": 7,
        "price_min_eur": 100.0,
        "price_typical_eur": 100.0,
        "price_max_eur": 100.0,
        "confidence": "low",
        "source": "TRO pilot calibration from user-provided München–Zürich DB observation",
        "is_prototype": True,
        "notes": "Very narrow prototype: München–Zürich DB itinerary, observed within one week.",
    },
    {
        "profile_key": "de-ch-international-200-399km-8-21d-db",
        "market": "DE-CH",
        "service_category": "international",
        "provider_context": "db",
        "distance_min_km": 200.0,
        "distance_max_km": 399.0,
        "booking_days_min": 8,
        "booking_days_max": 21,
        "price_min_eur": 50.0,
        "price_typical_eur": 50.0,
        "price_max_eur": 50.0,
        "confidence": "low",
        "source": "TRO pilot calibration from user-provided München–Zürich DB observation",
        "is_prototype": True,
        "notes": "Very narrow prototype: München–Zürich DB itinerary, observed about two weeks ahead.",
    },
    {
        "profile_key": "de-ch-international-200-399km-3-7d-oebb",
        "market": "DE-CH",
        "service_category": "international",
        "provider_context": "oebb",
        "distance_min_km": 200.0,
        "distance_max_km": 399.0,
        "booking_days_min": 3,
        "booking_days_max": 7,
        "price_min_eur": 129.0,
        "price_typical_eur": 129.0,
        "price_max_eur": 129.0,
        "confidence": "low",
        "source": "TRO pilot calibration from user-provided München–Zürich ÖBB observation",
        "is_prototype": True,
        "notes": "Very narrow prototype: München–Zürich ÖBB itinerary, observed within one week.",
    },
    {
        "profile_key": "de-ch-international-200-399km-8-21d-oebb",
        "market": "DE-CH",
        "service_category": "international",
        "provider_context": "oebb",
        "distance_min_km": 200.0,
        "distance_max_km": 399.0,
        "booking_days_min": 8,
        "booking_days_max": 21,
        "price_min_eur": 125.0,
        "price_typical_eur": 125.0,
        "price_max_eur": 125.0,
        "confidence": "low",
        "source": "TRO pilot calibration from user-provided München–Zürich ÖBB observation",
        "is_prototype": True,
        "notes": "Very narrow prototype: München–Zürich ÖBB itinerary, observed about two weeks ahead.",
    },
    {
        "profile_key": "de-long-distance-100-199km-0-2d",
        "market": "DE",
        "service_category": "long_distance",
        "distance_min_km": 100.0,
        "distance_max_km": 199.0,
        "booking_days_min": 0,
        "booking_days_max": 2,
        "price_min_eur": 30.0,
        "price_typical_eur": 60.0,
        "price_max_eur": 95.0,
        "confidence": "low",
        "source": "TRO pilot calibration from user-provided ICE observations",
        "is_prototype": True,
        "notes": "One adult, second class, no stated discount. Calibrated from München–Augsburg, Berlin–Leipzig and Frankfurt–Köln one day ahead.",
    },
    {
        "profile_key": "de-long-distance-100-199km-3-21d",
        "market": "DE",
        "service_category": "long_distance",
        "distance_min_km": 100.0,
        "distance_max_km": 199.0,
        "booking_days_min": 3,
        "booking_days_max": 21,
        "price_min_eur": 17.0,
        "price_typical_eur": 27.0,
        "price_max_eur": 35.0,
        "confidence": "low",
        "source": "TRO pilot calibration from user-provided ICE observations",
        "is_prototype": True,
        "notes": "One adult, second class, no stated discount. Calibrated from München–Augsburg, Berlin–Leipzig and Frankfurt–Köln about two weeks ahead.",
    },
    {
        "profile_key": "de-at-international-100-199km-0-2d-rjec",
        "market": "DE-AT",
        "service_category": "international",
        "distance_min_km": 100.0,
        "distance_max_km": 199.0,
        "booking_days_min": 0,
        "booking_days_max": 2,
        "price_min_eur": 22.0,
        "price_typical_eur": 22.0,
        "price_max_eur": 22.0,
        "confidence": "low",
        "source": "TRO pilot calibration from user-provided München–Salzburg RJ/EC observation",
        "is_prototype": True,
        "notes": "Very narrow prototype: one adult, no stated discount, München–Salzburg RJ/EC only. It must not be generalized beyond comparable services.",
    },
    {
        "profile_key": "de-at-international-100-199km-3-21d-rjec",
        "market": "DE-AT",
        "service_category": "international",
        "distance_min_km": 100.0,
        "distance_max_km": 199.0,
        "booking_days_min": 3,
        "booking_days_max": 21,
        "price_min_eur": 17.0,
        "price_typical_eur": 17.0,
        "price_max_eur": 17.0,
        "confidence": "low",
        "source": "TRO pilot calibration from user-provided München–Salzburg RJ/EC observation",
        "is_prototype": True,
        "notes": "Very narrow prototype: one adult, no stated discount, München–Salzburg RJ/EC only. It must not be generalized beyond comparable services.",
    },
    {
        "profile_key": "de-at-regional-100-199km-0-21d-re",
        "market": "DE-AT",
        "service_category": "regional",
        "distance_min_km": 100.0,
        "distance_max_km": 199.0,
        "booking_days_min": 0,
        "booking_days_max": 21,
        "price_min_eur": 30.0,
        "price_typical_eur": 30.0,
        "price_max_eur": 30.0,
        "confidence": "low",
        "source": "TRO pilot calibration from user-provided München–Salzburg RE observations",
        "is_prototype": True,
        "notes": "Very narrow prototype: non-Deutschlandticket RE observation for München–Salzburg. Cross-border ticket validity must be checked before use.",
    },
    {
        "profile_key": "de-long-distance-200-399km-0-2d",
        "market": "DE",
        "service_category": "long_distance",
        "distance_min_km": 200.0,
        "distance_max_km": 399.0,
        "booking_days_min": 0,
        "booking_days_max": 2,
        "price_min_eur": 75.0,
        "price_typical_eur": 90.0,
        "price_max_eur": 105.0,
        "confidence": "low",
        "source": "TRO pilot calibration from user-provided ICE observations",
        "is_prototype": True,
        "notes": "One adult, second class, no stated discount. Calibrated from Berlin–Hamburg, one day ahead.",
    },
    {
        "profile_key": "de-long-distance-200-399km-3-21d",
        "market": "DE",
        "service_category": "long_distance",
        "distance_min_km": 200.0,
        "distance_max_km": 399.0,
        "booking_days_min": 3,
        "booking_days_max": 21,
        "price_min_eur": 50.0,
        "price_typical_eur": 60.0,
        "price_max_eur": 70.0,
        "confidence": "low",
        "source": "TRO pilot calibration from user-provided ICE observations",
        "is_prototype": True,
        "notes": "One adult, second class, no stated discount. Calibrated from Berlin–Hamburg and Berlin–Kiel, about two weeks ahead.",
    },
    {
        "profile_key": "de-long-distance-400-599km-0-2d",
        "market": "DE",
        "service_category": "long_distance",
        "distance_min_km": 400.0,
        "distance_max_km": 599.0,
        "booking_days_min": 0,
        "booking_days_max": 2,
        "price_min_eur": 110.0,
        "price_typical_eur": 130.0,
        "price_max_eur": 150.0,
        "confidence": "low",
        "source": "TRO pilot calibration from user-provided ICE observations",
        "is_prototype": True,
        "notes": "One adult, second class, no stated discount. Calibrated from Berlin–Düsseldorf, one day ahead.",
    },
    {
        "profile_key": "de-long-distance-400-599km-3-21d",
        "market": "DE",
        "service_category": "long_distance",
        "distance_min_km": 400.0,
        "distance_max_km": 599.0,
        "booking_days_min": 3,
        "booking_days_max": 21,
        "price_min_eur": 50.0,
        "price_typical_eur": 70.0,
        "price_max_eur": 100.0,
        "confidence": "low",
        "source": "TRO pilot calibration from user-provided ICE observations",
        "is_prototype": True,
        "notes": "One adult, second class, no stated discount. Calibrated from München–Berlin, about one to two weeks ahead.",
    },
    {
        "profile_key": "de-long-distance-600-750km-0-2d",
        "market": "DE",
        "service_category": "long_distance",
        "distance_min_km": 600.0,
        "distance_max_km": 750.0,
        "booking_days_min": 0,
        "booking_days_max": 2,
        "price_min_eur": 70.0,
        "price_typical_eur": 100.0,
        "price_max_eur": 130.0,
        "confidence": "low",
        "source": "TRO pilot calibration from user-provided ICE observations",
        "is_prototype": True,
        "notes": "One adult, second class, no stated discount. Calibrated from Stuttgart–Hamburg, one day ahead.",
    },
    {
        "profile_key": "de-long-distance-600-750km-3-21d",
        "market": "DE",
        "service_category": "long_distance",
        "distance_min_km": 600.0,
        "distance_max_km": 750.0,
        "booking_days_min": 3,
        "booking_days_max": 21,
        "price_min_eur": 20.0,
        "price_typical_eur": 45.0,
        "price_max_eur": 70.0,
        "confidence": "low",
        "source": "TRO pilot calibration from user-provided ICE observations",
        "is_prototype": True,
        "notes": "One adult, second class, no stated discount. Calibrated from Stuttgart–Hamburg, about two weeks ahead.",
    },
    {
        "profile_key": "de-long-distance-400-750km-0-2d",
        "market": "DE",
        "service_category": "long_distance",
        "distance_min_km": 400.0,
        "distance_max_km": 750.0,
        "booking_days_min": 0,
        "booking_days_max": 2,
        "price_min_eur": 110.0,
        "price_typical_eur": 150.0,
        "price_max_eur": 190.0,
        "confidence": "low",
        "source": "TRO pilot calibration from user-reported München–Berlin ICE examples",
        "is_prototype": True,
        "notes": "Prototype only. Represents a one-adult, second-class, no-discount long-distance itinerary. It is not a live DB fare.",
    },
    {
        "profile_key": "de-long-distance-400-750km-3-14d",
        "market": "DE",
        "service_category": "long_distance",
        "distance_min_km": 400.0,
        "distance_max_km": 750.0,
        "booking_days_min": 3,
        "booking_days_max": 14,
        "price_min_eur": 50.0,
        "price_typical_eur": 70.0,
        "price_max_eur": 100.0,
        "confidence": "low",
        "source": "TRO pilot calibration from user-reported München–Berlin ICE examples",
        "is_prototype": True,
        "notes": "Prototype only. Represents a one-adult, second-class, no-discount long-distance itinerary. It is not a live DB fare.",
    },
)


def _utc_now() -> datetime:
    """Return the SQLite-compatible UTC timestamp used by profile records."""
    return datetime.now(UTC).replace(tzinfo=None)


DISTANCE_BANDS = (
    (0.0, 99.0), (100.0, 199.0), (200.0, 399.0), (400.0, 599.0),
    (600.0, 799.0), (800.0, 999.0), (1000.0, 1200.0), (1201.0, 1600.0),
)
BOOKING_BANDS = ((0, 2), (3, 7), (8, 21), (22, 60), (61, 365))
DEFAULT_TIME_OF_DAY_VARIATION_EUR = 20.0


def _matching_band(value: float, bands: tuple[tuple[float, float], ...]) -> tuple[float, float] | None:
    """Return the inclusive band containing a distance or booking-horizon value."""
    return next((band for band in bands if band[0] <= value <= band[1]), None)


def _confidence_for_sample_size(sample_size: int) -> str:
    """Label statistical confidence without overstating a small calibration set."""
    if sample_size >= 20:
        return "high"
    if sample_size >= 8:
        return "medium"
    if sample_size >= 3:
        return "low"
    return "very_low"


class RailPriceProfileRepository:
    """Reads and seeds rail-price profiles without storing individual tickets."""

    def seed_pilot_profiles(self) -> int:
        """Insert the documented pilot profiles once and preserve later edits."""
        inserted = 0
        with SessionLocal() as session:
            for payload in PILOT_PROFILES:
                existing = session.scalar(
                    select(RailPriceProfileRecord).where(
                        RailPriceProfileRecord.profile_key == payload["profile_key"]
                    )
                )
                if existing is not None:
                    continue
                session.add(RailPriceProfileRecord(**payload, updated_at=_utc_now()))
                inserted += 1
            session.commit()
        return inserted

    def rebuild_derived_profiles(self) -> int:
        """Recalculate statistical profiles from classified rail-price observations.

        Only observations with a known market, service category and route
        distance participate.  Existing hand-curated pilot profiles remain as
        fallbacks; only previous profiles whose key starts with ``derived:``
        are replaced.  This makes repeated imports safe and auditable.
        """
        grouped: dict[tuple, list[RailPriceObservationRecord]] = defaultdict(list)
        with SessionLocal() as session:
            observations = session.scalars(
                select(RailPriceObservationRecord).where(
                    RailPriceObservationRecord.market.is_not(None),
                    RailPriceObservationRecord.service_category.is_not(None),
                    RailPriceObservationRecord.estimated_distance_km.is_not(None),
                    RailPriceObservationRecord.is_example.is_(False),
                )
            ).all()
            for observation in observations:
                distance_band = _matching_band(observation.estimated_distance_km, DISTANCE_BANDS)
                booking_band = _matching_band(float(observation.booking_days_ahead), BOOKING_BANDS)
                if distance_band is None or booking_band is None:
                    continue
                provider = (observation.provider_context or "any").casefold()
                grouped[
                    (
                        observation.market.upper(),
                        observation.service_category,
                        provider,
                        distance_band,
                        booking_band,
                    )
                ].append(observation)

            session.execute(
                delete(RailPriceProfileRecord).where(
                    RailPriceProfileRecord.profile_key.like("derived:%")
                )
            )
            for (market, service, provider, distance_band, booking_band), rows in grouped.items():
                typical = float(median(row.price_eur for row in rows))
                lower_observed = min(
                    row.price_min_eur if row.price_min_eur is not None else row.price_eur
                    for row in rows
                )
                upper_observed = max(
                    row.price_max_eur if row.price_max_eur is not None else row.price_eur
                    for row in rows
                )
                minimum = max(0.0, min(lower_observed, typical - DEFAULT_TIME_OF_DAY_VARIATION_EUR))
                maximum = max(upper_observed, typical + DEFAULT_TIME_OF_DAY_VARIATION_EUR)
                sample_size = len(rows)
                distance_key = f"{int(distance_band[0])}-{int(distance_band[1])}km"
                booking_key = f"{int(booking_band[0])}-{int(booking_band[1])}d"
                session.add(
                    RailPriceProfileRecord(
                        profile_key=f"derived:{market.casefold()}:{service}:{provider}:{distance_key}:{booking_key}",
                        market=market,
                        service_category=service,
                        provider_context=provider,
                        distance_min_km=distance_band[0],
                        distance_max_km=distance_band[1],
                        booking_days_min=int(booking_band[0]),
                        booking_days_max=int(booking_band[1]),
                        price_min_eur=round(minimum, 2),
                        price_typical_eur=round(typical, 2),
                        price_max_eur=round(maximum, 2),
                        confidence=_confidence_for_sample_size(sample_size),
                        sample_size=sample_size,
                        source=f"TRO statistical median from {sample_size} classified observation(s)",
                        is_prototype=False,
                        notes=(
                            "Derived from route-level price observations. "
                            f"Range includes a EUR {DEFAULT_TIME_OF_DAY_VARIATION_EUR:.0f} time-of-day allowance. "
                            "Not a live fare or availability confirmation."
                        ),
                        updated_at=_utc_now(),
                    )
                )
            session.commit()
        return len(grouped)

    def find_profile(
        self, market: str, service_category: str, distance_km: float,
        booking_days_ahead: int, provider_context: str | None = None,
    ) -> RailPriceProfileRecord | None:
        """Return the narrowest matching profile for a journey's market segment."""
        requested_provider = (provider_context or "any").strip().casefold()
        with SessionLocal() as session:
            profiles = list(session.scalars(
                select(RailPriceProfileRecord).where(
                    RailPriceProfileRecord.market == market.upper(),
                    RailPriceProfileRecord.service_category == service_category,
                    RailPriceProfileRecord.distance_min_km <= distance_km,
                    RailPriceProfileRecord.distance_max_km >= distance_km,
                    RailPriceProfileRecord.booking_days_min <= booking_days_ahead,
                    RailPriceProfileRecord.booking_days_max >= booking_days_ahead,
                    or_(
                        RailPriceProfileRecord.provider_context == "any",
                        RailPriceProfileRecord.provider_context == requested_provider,
                    ),
                )
            ))
            if not profiles:
                return None
            return min(
                profiles,
                key=lambda profile: (
                    profile.distance_max_km - profile.distance_min_km,
                    profile.booking_days_max - profile.booking_days_min,
                    profile.provider_context != requested_provider,
                    not profile.profile_key.startswith("derived:"),
                ),
            )


class RailPriceEstimator:
    """Creates explicit rail-price ranges from saved market profiles."""

    def __init__(self, repository: RailPriceProfileRepository | None = None) -> None:
        """Allow a repository to be injected for focused unit tests."""
        self._repository = repository or RailPriceProfileRepository()

    def estimate(
        self, *, market: str, service_category: str, distance_km: float,
        booking_days_ahead: int, passengers: int = 1,
        has_deutschlandticket: bool = False,
        provider_context: str | None = None,
    ) -> RailPriceEstimate | None:
        """Estimate a whole ticket using a matching profile or ticket coverage rule."""
        if has_deutschlandticket and service_category == "regional":
            return RailPriceEstimate(
                minimum_eur=0.0,
                typical_eur=0.0,
                maximum_eur=0.0,
                confidence="high",
                sample_size=None,
                price_status="covered_by_existing_ticket",
                profile_key=None,
                source="Deutschlandticket declared by traveller",
                disclaimer="The marginal fare is zero only because the traveller already has a valid Deutschlandticket.",
            )
        profile = self._repository.find_profile(
            market, service_category, distance_km, booking_days_ahead, provider_context
        )
        if profile is None:
            return None
        multiplier = max(1, passengers)
        return RailPriceEstimate(
            minimum_eur=round(profile.price_min_eur * multiplier, 2),
            typical_eur=round(profile.price_typical_eur * multiplier, 2),
            maximum_eur=round(profile.price_max_eur * multiplier, 2),
            confidence=profile.confidence,
            sample_size=profile.sample_size,
            price_status=(
                "statistical_profile_estimate"
                if profile.profile_key.startswith("derived:")
                else "prototype_profile_estimate" if profile.is_prototype else "profile_estimate"
            ),
            profile_key=profile.profile_key,
            source=profile.source,
            disclaimer="This is an estimated price range for the complete itinerary, not a live fare or availability confirmation.",
        )


rail_price_profile_repository = RailPriceProfileRepository()
rail_price_estimator = RailPriceEstimator(rail_price_profile_repository)
