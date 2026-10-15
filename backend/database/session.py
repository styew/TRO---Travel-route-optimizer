"""Configure SQLAlchemy sessions from the environment-specific database URL."""

import os

from dotenv import load_dotenv
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import DeclarativeBase, sessionmaker

load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./tro.db")
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
    if engine.dialect.name != "sqlite":
        return
    city_columns = {column["name"] for column in inspect(engine).get_columns("cities")}
    if "infrastructure_filter_version" not in city_columns:
        with engine.begin() as connection:
            connection.execute(text("ALTER TABLE cities ADD COLUMN infrastructure_filter_version INTEGER NOT NULL DEFAULT 1"))
