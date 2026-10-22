"""Database connectivity, through SQLAlchemy over pyodbc."""

from __future__ import annotations

import logging
import re
import time

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import SQLAlchemyError

from .config import DatabaseConfig

logger = logging.getLogger(__name__)

_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def validate_identifier(value: str, kind: str = "identifier") -> str:
    """Return `value` if it is a safe SQL identifier, raise otherwise.

    Table and database names cannot be bound as query parameters, so the few
    places that interpolate one check it against this instead of escaping it.
    """
    if not _IDENTIFIER.match(value or ""):
        raise ValueError(f"Invalid {kind}: {value!r}")
    return value


def create_engine_for(config: DatabaseConfig, database: str | None = None) -> Engine:
    return create_engine(config.sqlalchemy_url(database), fast_executemany=True)


def wait_for_connection(engine: Engine, attempts: int = 3, delay: int = 3) -> bool:
    """Try the connection a few times before giving up.

    This runs unattended, so a database that is still starting up should cost a
    few seconds rather than the whole run.
    """
    for attempt in range(1, attempts + 1):
        try:
            with engine.connect():
                logger.info("Connected to the database")
                return True
        except SQLAlchemyError as exc:
            logger.warning("Connection attempt %d/%d failed: %s", attempt, attempts, exc)
            if attempt < attempts:
                time.sleep(delay)
    return False


def ensure_database(config: DatabaseConfig) -> None:
    """Create the destination database on first run.

    CREATE DATABASE cannot run inside a transaction, so this connects to
    `master` in autocommit mode.
    """
    name = validate_identifier(config.database, "database name")
    engine = create_engine_for(config, database="master")

    with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as connection:
        exists = connection.execute(text("SELECT DB_ID(:name)"), {"name": name}).scalar()
        if exists is None:
            logger.info("Creating database %r", name)
            connection.execute(text(f"CREATE DATABASE [{name}]"))
        else:
            logger.info("Database %r already exists", name)

    engine.dispose()
