"""The load-control table: what has already been loaded, and up to when.

This is what makes the pipeline incremental. Every processed email is recorded
with the subject it arrived under and the time it was received; the next run
reads back the most recent timestamp per subject and asks the mailbox only for
what came after it. Nothing is downloaded twice, and a run that dies halfway
resumes from the last email it actually loaded.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta

from sqlalchemy import text
from sqlalchemy.engine import Engine

logger = logging.getLogger(__name__)

CONTROL_TABLE = "etl_load_history"

CONTROL_TABLE_DDL = f"""
IF OBJECT_ID('dbo.{CONTROL_TABLE}', 'U') IS NULL
CREATE TABLE dbo.{CONTROL_TABLE} (
    subject NVARCHAR(255) NOT NULL,
    received_at DATETIME2 NOT NULL,
    attachment NVARCHAR(400) NOT NULL,
    destination_table NVARCHAR(128) NOT NULL,
    rows_loaded INT NOT NULL,
    loaded_at DATETIME2 NOT NULL DEFAULT SYSUTCDATETIME()
);
"""

# One row per subject: the latest email already loaded under it. ROW_NUMBER
# ranks each subject's history by arrival, and only the newest survives.
LATEST_PER_SUBJECT = f"""
WITH ranked AS (
    SELECT
        subject,
        received_at,
        ROW_NUMBER() OVER (PARTITION BY subject ORDER BY received_at DESC) AS rn
    FROM dbo.{CONTROL_TABLE}
)
SELECT subject, received_at FROM ranked WHERE rn = 1
"""


def ensure_control_table(engine: Engine) -> None:
    with engine.begin() as connection:
        connection.execute(text(CONTROL_TABLE_DDL))
    logger.info("Control table dbo.%s verified", CONTROL_TABLE)


def watermarks(engine: Engine) -> dict[str, datetime]:
    """The most recent email already loaded, per subject."""
    with engine.connect() as connection:
        rows = connection.execute(text(LATEST_PER_SUBJECT)).all()
    found = {row.subject: row.received_at for row in rows}
    logger.info("Read %d watermark(s) from the control table", len(found))
    return found


def watermark_for(
    marks: dict[str, datetime], subject: str, lookback_days: int
) -> datetime:
    """Where to resume for this subject.

    A subject nobody has loaded yet has no watermark, so it starts from a
    bounded lookback instead of the whole mailbox.
    """
    if subject in marks:
        return marks[subject]
    start = datetime.now() - timedelta(days=lookback_days)
    logger.info("%s: first run, looking back to %s", subject, start.date())
    return start


def record_load(
    engine: Engine,
    subject: str,
    received_at: datetime,
    attachment: str,
    destination_table: str,
    rows_loaded: int,
) -> None:
    """Mark one attachment as loaded, moving the watermark forward."""
    with engine.begin() as connection:
        connection.execute(
            text(
                f"INSERT INTO dbo.{CONTROL_TABLE} "
                f"(subject, received_at, attachment, destination_table, rows_loaded, loaded_at) "
                f"VALUES (:subject, :received_at, :attachment, :destination_table, "
                f":rows_loaded, :loaded_at)"
            ),
            {
                "subject": subject,
                "received_at": received_at,
                "attachment": attachment,
                "destination_table": destination_table,
                "rows_loaded": rows_loaded,
                "loaded_at": datetime.now(),
            },
        )
