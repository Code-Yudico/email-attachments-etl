"""Pipeline entry point: read the mailbox, shape the attachments, load them.

Run with `python -m src.pipeline`. Add `--dry-run` to do everything except
write to the database, which is useful for checking what a mailbox holds.
"""

from __future__ import annotations

import argparse
import logging
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

from . import attachments, control, db, load, mailbox, transform
from .config import ConfigError, PipelineConfig, load_config
from .reports import Report, ReportConfigError, load_reports

logger = logging.getLogger(__name__)

LOG_DIR = Path(__file__).resolve().parent.parent / "logs"


def setup_logging(verbose: bool = False) -> None:
    LOG_DIR.mkdir(exist_ok=True)
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s - %(levelname)s - %(message)s",
        handlers=[
            logging.StreamHandler(sys.stdout),
            logging.FileHandler(LOG_DIR / "etl.log", encoding="utf-8"),
        ],
    )


@dataclass
class Summary:
    emails: int = 0
    attachments: int = 0
    rows: int = 0
    skipped: list[str] = field(default_factory=list)


def process_report(
    client,
    engine,
    report: Report,
    config: PipelineConfig,
    marks: dict,
    summary: Summary,
    dry_run: bool,
) -> None:
    """Handle every new email for one report."""
    since = control.watermark_for(marks, report.subject, config.initial_lookback_days)
    emails = mailbox.fetch_since(client, report.subject, since)

    for message in emails:
        summary.emails += 1
        files = attachments.materialise(
            message.attachments, config.work_dir, message.received_at
        )

        for file in files:
            try:
                frame = transform.transform_attachment(file, report)
            except transform.TransformError as exc:
                # One malformed attachment must not stop the others, but it
                # must be visible and must not move the watermark.
                logger.error("%s", exc)
                summary.skipped.append(f"{file.original_name}: {exc}")
                continue

            if frame is None:
                summary.skipped.append(f"{file.original_name}: no matching rule")
                continue

            summary.attachments += 1
            if dry_run:
                logger.info("  %s: %d row(s) would be loaded", report.table, len(frame))
                summary.rows += len(frame)
                continue

            rows = load.load_dataframe(engine, frame, report)
            summary.rows += rows
            control.record_load(
                engine,
                subject=report.subject,
                received_at=message.received_at,
                attachment=file.original_name,
                destination_table=report.table,
                rows_loaded=rows,
            )


def run(config: PipelineConfig, dry_run: bool = False) -> int:
    started = time.time()
    reports = load_reports()

    logger.info("=" * 68)
    logger.info("EMAIL ATTACHMENTS ETL - starting%s", " (dry run)" if dry_run else "")
    logger.info("Mailbox: %s@%s", config.mailbox.user, config.mailbox.host)
    if not dry_run:
        logger.info("Destination: %s / %s", config.database.server, config.database.database)
    logger.info("Reports: %s", ", ".join(report.name for report in reports))
    logger.info("=" * 68)

    engine = None
    marks: dict = {}

    if not dry_run:
        db.ensure_database(config.database)
        engine = db.create_engine_for(config.database)
        if not db.wait_for_connection(engine):
            logger.error("Could not reach the database. Stopping.")
            return 1
        control.ensure_control_table(engine)
        marks = control.watermarks(engine)

    summary = Summary()
    try:
        with mailbox.open_mailbox(config.mailbox) as client:
            for report in reports:
                process_report(client, engine, report, config, marks, summary, dry_run)
    except mailbox.MailboxError as exc:
        logger.error("Mailbox error: %s", exc)
        return 1
    finally:
        if engine is not None:
            engine.dispose()

    logger.info("=" * 68)
    logger.info("Completed in %.2fs", time.time() - started)
    logger.info("  %d email(s), %d attachment(s), %d row(s)",
                summary.emails, summary.attachments, summary.rows)
    for problem in summary.skipped:
        logger.warning("  skipped %s", problem)
    logger.info("=" * 68)

    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Email attachments to SQL Server ETL")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="read and shape the attachments without writing to the database",
    )
    parser.add_argument("-v", "--verbose", action="store_true", help="debug logging")
    args = parser.parse_args()

    setup_logging(args.verbose)

    try:
        config = load_config()
    except ConfigError as exc:
        logger.error("Configuration error: %s", exc)
        return 2

    try:
        return run(config, dry_run=args.dry_run)
    except ReportConfigError as exc:
        logger.error("Report configuration error: %s", exc)
        return 2
    except Exception as exc:
        logger.exception("Pipeline failed: %s", exc)
        return 1


if __name__ == "__main__":
    sys.exit(main())
