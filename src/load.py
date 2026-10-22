"""Loading a shaped DataFrame into its destination table."""

from __future__ import annotations

import logging

import pandas as pd
from sqlalchemy.engine import Engine
from sqlalchemy.exc import SQLAlchemyError

from .db import validate_identifier
from .reports import Report

logger = logging.getLogger(__name__)


class LoadError(RuntimeError):
    """The destination table could not be written."""


def load_dataframe(
    engine: Engine, frame: pd.DataFrame, report: Report, batch_size: int = 1000
) -> int:
    """Append the rows to the report's table, creating it if needed.

    Appending is safe here because the mailbox watermark already guarantees an
    email is processed once: the control table, not the data table, is what
    makes a rerun a no-op.
    """
    table = validate_identifier(report.table, "table name")

    if frame.empty:
        logger.warning("%s: nothing to load", table)
        return 0

    try:
        frame.to_sql(
            table,
            con=engine,
            schema="dbo",
            if_exists="append",
            index=False,
            chunksize=batch_size,
            method=None,
        )
    except SQLAlchemyError as exc:
        raise LoadError(f"Could not load {len(frame)} row(s) into {table}: {exc}") from exc

    logger.info("  %s: %d row(s) loaded", table, len(frame))
    return len(frame)
