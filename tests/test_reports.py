"""Tests for the declarative report configuration."""

from __future__ import annotations

import pytest

from src.reports import ReportConfigError, load_reports

VALID = """
daily_sales:
  subject: "Reporte diario de ventas"
  table: branch_sales
  date_column: sale_date
  files:
    - pattern: "ventas_*.txt"
      format: delimited
      encoding: latin-1
      separator: "|"
    - pattern: "ventas_*.xlsx"
      format: excel
  columns:
    FECHA: sale_date
    UNIDADES: units
  dtypes:
    units: int64
"""


def _write(tmp_path, body: str):
    path = tmp_path / "reports.yaml"
    path.write_text(body, encoding="utf-8")
    return path


def test_loads_a_report_with_several_file_rules(tmp_path) -> None:
    (report,) = load_reports(_write(tmp_path, VALID))

    assert report.subject == "Reporte diario de ventas"
    assert report.table == "branch_sales"
    assert len(report.files) == 2

    delimited = report.files[0]
    assert delimited.encoding == "latin-1"
    assert delimited.separator == "|"


def test_rule_is_matched_case_insensitively_by_pattern(tmp_path) -> None:
    (report,) = load_reports(_write(tmp_path, VALID))

    assert report.rule_for("ventas_2026-10-20.txt").format == "delimited"
    assert report.rule_for("VENTAS_2026-10-20.XLSX").format == "excel"
    assert report.rule_for("inventario_2026-10-20.xlsx") is None


def test_missing_configuration_file_is_reported(tmp_path) -> None:
    with pytest.raises(ReportConfigError, match="not found"):
        load_reports(tmp_path / "does-not-exist.yaml")


def test_report_without_required_keys_is_rejected(tmp_path) -> None:
    body = """
broken:
  subject: "Algo"
  files:
    - pattern: "*.csv"
"""
    with pytest.raises(ReportConfigError, match="missing"):
        load_reports(_write(tmp_path, body))


def test_unsupported_format_is_rejected(tmp_path) -> None:
    body = """
broken:
  subject: "Algo"
  table: t
  files:
    - pattern: "*.pdf"
      format: pdf
  columns:
    A: a
"""
    with pytest.raises(ReportConfigError, match="unsupported format"):
        load_reports(_write(tmp_path, body))


def test_two_reports_cannot_share_a_subject(tmp_path) -> None:
    """The incremental watermark is tracked per subject, so it must be unique."""
    body = """
first:
  subject: "Mismo asunto"
  table: a
  files:
    - pattern: "*.csv"
  columns:
    A: a
second:
  subject: "Mismo asunto"
  table: b
  files:
    - pattern: "*.txt"
  columns:
    B: b
"""
    with pytest.raises(ReportConfigError, match="share a subject"):
        load_reports(_write(tmp_path, body))
