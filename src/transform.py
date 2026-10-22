"""Turning an attachment into a DataFrame shaped like its destination table."""

from __future__ import annotations

import logging
from pathlib import Path

import pandas as pd

from .attachments import LocalFile
from .reports import FileRule, Report

logger = logging.getLogger(__name__)


class TransformError(RuntimeError):
    """The attachment could not be read or did not match its configuration."""


def read_file(path: Path, rule: FileRule) -> pd.DataFrame:
    """Read one attachment according to its rule."""
    try:
        if rule.format == "excel":
            frame = pd.read_excel(path)
        else:
            frame = pd.read_csv(path, sep=rule.separator, encoding=rule.encoding)
    except Exception as exc:
        raise TransformError(f"Could not read {path.name}: {exc}") from exc

    frame = frame.dropna(how="all")
    logger.info("Read %s (%d rows, %d columns)", path.name, len(frame), len(frame.columns))
    return frame


def apply_report(frame: pd.DataFrame, report: Report) -> pd.DataFrame:
    """Rename the source columns and coerce the configured types."""
    # Headers arrive upper case but not consistently, so comparison is done on
    # a normalised copy rather than trusting the export.
    normalised = {str(column).strip().upper(): column for column in frame.columns}

    missing = [source for source in report.columns if source not in normalised]
    if missing:
        raise TransformError(
            f"{report.name}: the attachment is missing expected column(s) {missing}"
        )

    selected = frame[[normalised[source] for source in report.columns]].copy()
    selected.columns = list(report.columns.values())

    if report.date_column and report.date_column in selected.columns:
        selected[report.date_column] = pd.to_datetime(
            selected[report.date_column], errors="coerce", dayfirst=True
        )
        unparsed = int(selected[report.date_column].isna().sum())
        if unparsed:
            logger.warning("%s: %d row(s) with an unreadable date", report.name, unparsed)

    for column, dtype in report.dtypes.items():
        if column not in selected.columns:
            continue
        try:
            if dtype.startswith("int"):
                selected[column] = (
                    pd.to_numeric(selected[column], errors="coerce").fillna(0).astype(dtype)
                )
            elif dtype.startswith("float"):
                selected[column] = pd.to_numeric(selected[column], errors="coerce").astype(dtype)
            else:
                selected[column] = selected[column].astype(dtype)
        except (TypeError, ValueError) as exc:
            raise TransformError(
                f"{report.name}: column {column!r} could not be cast to {dtype}: {exc}"
            ) from exc

    return selected


def transform_attachment(file: LocalFile, report: Report) -> pd.DataFrame | None:
    """Read and shape one attachment, or return None if the report ignores it.

    The rule is matched on the name the file arrived with, not on the stored
    path, which carries a timestamp prefix this pipeline added.
    """
    rule = report.rule_for(file.original_name)
    if rule is None:
        logger.warning("%s: no rule matches %s, skipping", report.name, file.original_name)
        return None
    return apply_report(read_file(file.path, rule), report)
