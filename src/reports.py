"""Report definitions, loaded from `config/reports.yaml`.

Each report says which subject to look for in the mailbox, how to parse the
attachments that arrive under it, how to rename their columns and where to
load them. Supporting a new report is an edit to the YAML; the pipeline below
does not change.
"""

from __future__ import annotations

import fnmatch
from dataclasses import dataclass
from pathlib import Path

import yaml

CONFIG_PATH = Path(__file__).resolve().parent.parent / "config" / "reports.yaml"


class ReportConfigError(ValueError):
    """The report configuration is missing or malformed."""


@dataclass(frozen=True)
class FileRule:
    """How to recognise and parse one kind of attachment."""

    pattern: str
    format: str
    encoding: str = "utf-8"
    separator: str = ","

    def matches(self, filename: str) -> bool:
        return fnmatch.fnmatch(filename.lower(), self.pattern.lower())


@dataclass(frozen=True)
class Report:
    name: str
    subject: str
    table: str
    date_column: str
    files: tuple[FileRule, ...]
    columns: dict[str, str]
    dtypes: dict[str, str]

    def rule_for(self, filename: str) -> FileRule | None:
        """The rule that applies to this attachment, if any."""
        for rule in self.files:
            if rule.matches(filename):
                return rule
        return None


SUPPORTED_FORMATS = {"delimited", "excel"}


def _build_report(name: str, raw: dict) -> Report:
    missing = {"subject", "table", "columns", "files"} - raw.keys()
    if missing:
        raise ReportConfigError(f"Report {name!r} is missing: {sorted(missing)}")

    rules = []
    for entry in raw["files"]:
        file_format = entry.get("format", "delimited")
        if file_format not in SUPPORTED_FORMATS:
            raise ReportConfigError(
                f"Report {name!r} uses unsupported format {file_format!r}. "
                f"Supported: {sorted(SUPPORTED_FORMATS)}"
            )
        rules.append(
            FileRule(
                pattern=entry["pattern"],
                format=file_format,
                encoding=entry.get("encoding", "utf-8"),
                separator=entry.get("separator", ","),
            )
        )

    return Report(
        name=name,
        subject=raw["subject"],
        table=raw["table"],
        date_column=raw.get("date_column", ""),
        files=tuple(rules),
        columns=dict(raw["columns"]),
        dtypes=dict(raw.get("dtypes", {})),
    )


def load_reports(path: Path = CONFIG_PATH) -> tuple[Report, ...]:
    """Read every report definition from the YAML file."""
    if not path.is_file():
        raise ReportConfigError(f"Report configuration not found at {path}")

    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(raw, dict) or not raw:
        raise ReportConfigError(f"{path} does not define any report")

    reports = tuple(_build_report(name, body) for name, body in raw.items())

    subjects = [report.subject for report in reports]
    duplicates = {subject for subject in subjects if subjects.count(subject) > 1}
    if duplicates:
        # Two reports under one subject would make the incremental watermark
        # ambiguous, since it is tracked per subject.
        raise ReportConfigError(f"Several reports share a subject: {sorted(duplicates)}")

    return reports
