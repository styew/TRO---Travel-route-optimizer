"""Configure SQLAlchemy sessions from the environment-specific database URL."""

import os

from dotenv import load_dotenv
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import DeclarativeBase, sessionmaker

load_dotenv()

# Keep direct maintenance scripts pointed at the same SQLite file mounted by
# Docker Compose.  Docker still provides its explicit /app/data URL.
DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./data/tro.db")
engine_options: dict = {}

if DATABASE_URL.startswith("sqlite"):
    engine_options["connect_args"] = {"check_same_thread": False}

engine = create_engine(DATABASE_URL, **engine_options)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)


class Base(DeclarativeBase):
    """Base class shared by all persistent database models."""


def initialize_database() -> None:
    """Create tables and apply the small SQLite cache migration used during development."""
    from backend.database import models  # noqa: F401

    Base.metadata.create_all(bind=engine)
    if engine.dialect.name == "sqlite":
        city_columns = {column["name"] for column in inspect(engine).get_columns("cities")}
        if "infrastructure_filter_version" not in city_columns:
            with engine.begin() as connection:
                connection.execute(text("ALTER TABLE cities ADD COLUMN infrastructure_filter_version INTEGER NOT NULL DEFAULT 1"))
        rail_observation_columns = {
            column["name"]
            for column in inspect(engine).get_columns("rail_price_observations")
        }
        with engine.begin() as connection:
            if "price_min_eur" not in rail_observation_columns:
                connection.execute(text("ALTER TABLE rail_price_observations ADD COLUMN price_min_eur FLOAT"))
            if "price_max_eur" not in rail_observation_columns:
                connection.execute(text("ALTER TABLE rail_price_observations ADD COLUMN price_max_eur FLOAT"))
            if "market" not in rail_observation_columns:
                connection.execute(text("ALTER TABLE rail_price_observations ADD COLUMN market VARCHAR(30)"))
            if "service_category" not in rail_observation_columns:
                connection.execute(text("ALTER TABLE rail_price_observations ADD COLUMN service_category VARCHAR(50)"))
            if "provider_context" not in rail_observation_columns:
                connection.execute(text("ALTER TABLE rail_price_observations ADD COLUMN provider_context VARCHAR(100)"))
            if "estimated_distance_km" not in rail_observation_columns:
                connection.execute(text("ALTER TABLE rail_price_observations ADD COLUMN estimated_distance_km FLOAT"))
        rail_profile_columns = {
            column["name"]
            for column in inspect(engine).get_columns("rail_price_profiles")
        }
        if "provider_context" not in rail_profile_columns:
            with engine.begin() as connection:
                connection.execute(
                    text(
                        "ALTER TABLE rail_price_profiles "
                        "ADD COLUMN provider_context VARCHAR(100) NOT NULL DEFAULT 'any'"
                    )
                )
        if "sample_size" not in rail_profile_columns:
            with engine.begin() as connection:
                connection.execute(text("ALTER TABLE rail_price_profiles ADD COLUMN sample_size INTEGER"))
    # The pilot profiles are tiny, explicit assumptions.  They are seeded once
    # so API users can inspect the estimator without filling route prices.
    from backend.services.rail_price_estimator import rail_price_profile_repository

    rail_price_profile_repository.seed_pilot_profiles()
