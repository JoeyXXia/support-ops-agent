import os
from pathlib import Path

from dotenv import load_dotenv
from sqlalchemy import create_engine
from sqlalchemy.engine import make_url


def database_url():
    root = Path(__file__).resolve().parents[2]
    load_dotenv(root / ".env.database.local", override=False)
    value = os.getenv("DATABASE_URL", "")
    if not value:
        raise ValueError("DATABASE_URL is required; see .env.example")
    url = make_url(value)
    if url.drivername != "postgresql+psycopg":
        raise ValueError("DATABASE_URL must use postgresql+psycopg")
    return url


def engine_from_environment():
    return create_engine(database_url(), pool_pre_ping=True, hide_parameters=True)
