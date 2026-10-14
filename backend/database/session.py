"""Configure SQLAlchemy sessions from the environment-specific database URL."""

import os

from dotenv import load_dotenv
from sqlalchemy import create_engine
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
    """Create missing local tables during development before migrations are introduced."""
    from backend.database import models  # noqa: F401

    Base.metadata.create_all(bind=engine)
