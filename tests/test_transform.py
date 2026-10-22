"""Tests for shaping an attachment into its destination table."""

from __future__ import annotations

import pandas as pd
import pytest

from src.attachments import LocalFile
from src.reports import FileRule, Report
from src.transform import TransformError, apply_report, read_file, transform_attachment

REPORT = Report(
    name="daily_branch_sales",
    subject="Reporte diario de ventas",
    table="branch_sales",
    date_column="sale_date",
    files=(
        FileRule(pattern="ventas_sucursal_*.txt", format="delimited",
                 encoding="latin-1", separator="|"),
    ),
    columns={
        "FECHA": "sale_date",
        "NOM_SUCURSAL": "branch_name",
        "UNIDADES": "units",
        "IMPORTE_S_IMP": "amount_net",
    },
    dtypes={"branch_name": "str", "units": "int64", "amount_net": "float64"},
)


def _export() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "FECHA": ["20/10/2026", "20/10/2026"],
            "NOM_SUCURSAL": ["León Plaza", "Mérida Norte"],
            "UNIDADES": ["5", "3"],
            "IMPORTE_S_IMP": ["1200.50", "800.00"],
            "COLUMNA_IGNORADA": ["x", "y"],
        }
    )


def test_columns_are_renamed_and_extras_dropped() -> None:
    result = apply_report(_export(), REPORT)

    assert list(result.columns) == ["sale_date", "branch_name", "units", "amount_net"]
    assert "COLUMNA_IGNORADA" not in result.columns


def test_types_are_coerced_as_configured() -> None:
    result = apply_report(_export(), REPORT)

    assert result["units"].dtype == "int64"
    assert result["amount_net"].dtype == "float64"
    assert result["units"].tolist() == [5, 3]


def test_dates_are_read_day_first() -> None:
    """The source writes DD/MM/YYYY, which pandas would otherwise misread."""
    result = apply_report(_export(), REPORT)

    assert str(result["sale_date"].iloc[0].date()) == "2026-10-20"


def test_headers_are_matched_ignoring_case_and_padding() -> None:
    export = _export().rename(columns={"NOM_SUCURSAL": " nom_sucursal "})

    result = apply_report(export, REPORT)

    assert result["branch_name"].tolist() == ["León Plaza", "Mérida Norte"]


def test_a_missing_expected_column_is_an_error() -> None:
    """Silently loading a truncated export is worse than failing loudly."""
    export = _export().drop(columns=["UNIDADES"])

    with pytest.raises(TransformError, match="missing expected column"):
        apply_report(export, REPORT)


def test_reads_a_latin1_pipe_separated_file(tmp_path) -> None:
    path = tmp_path / "ventas_sucursal_2026-10-20.txt"
    path.write_bytes(
        "FECHA|NOM_SUCURSAL|UNIDADES|IMPORTE_S_IMP\n"
        "20/10/2026|León Plaza|5|1200.50\n".encode("latin-1")
    )

    frame = read_file(path, REPORT.files[0])

    assert frame["NOM_SUCURSAL"].iloc[0] == "León Plaza"


def test_rules_are_matched_on_the_name_the_file_arrived_with(tmp_path) -> None:
    """Stored files carry a timestamp prefix that must not affect matching."""
    stored = tmp_path / "20261020_063000__ventas_sucursal_2026-10-20.txt"
    stored.write_bytes(
        "FECHA|NOM_SUCURSAL|UNIDADES|IMPORTE_S_IMP\n"
        "20/10/2026|León Plaza|5|1200.50\n".encode("latin-1")
    )
    local = LocalFile(path=stored, original_name="ventas_sucursal_2026-10-20.txt")

    result = transform_attachment(local, REPORT)

    assert result is not None
    assert len(result) == 1


def test_an_unknown_attachment_is_skipped_not_failed(tmp_path) -> None:
    local = LocalFile(path=tmp_path / "otra_cosa.pdf", original_name="otra_cosa.pdf")

    assert transform_attachment(local, REPORT) is None
