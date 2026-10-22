"""Tests for reading messages, and for the attachment handling around them."""

from __future__ import annotations

import email
import zipfile
from datetime import datetime, timezone

import pytest

from src.attachments import LocalFile, extract_zip, materialise
from src.mailbox import Attachment, _attachments, _decode, _received_at

RECEIVED = datetime(2026, 10, 20, 6, 30, 0)


def _message(headers: str, body: str = "cuerpo") -> email.message.Message:
    return email.message_from_string(f"{headers}\n\n{body}")


def test_received_at_is_naive_for_an_offset_timestamp() -> None:
    message = _message("Date: Tue, 20 Oct 2026 06:30:00 +0200")

    result = _received_at(message)

    assert result.tzinfo is None
    assert result == datetime(2026, 10, 20, 4, 30, 0)  # converted to UTC


def test_received_at_is_naive_when_the_header_is_missing() -> None:
    """The fallback has to be naive too, or the watermark comparison raises."""
    result = _received_at(_message("Subject: sin fecha"))

    assert result.tzinfo is None


def test_received_at_is_naive_when_the_header_is_unreadable() -> None:
    result = _received_at(_message("Date: no es una fecha"))

    assert result.tzinfo is None


def test_received_at_never_mixes_with_an_aware_now() -> None:
    """A regression guard: comparing the two is what used to blow up."""
    result = _received_at(_message("Date: Tue, 20 Oct 2026 06:30:00 +0000"))

    assert result < datetime.now(timezone.utc).replace(tzinfo=None)


def test_encoded_subjects_are_decoded() -> None:
    encoded = "=?utf-8?q?Movimientos_de_inventario?="

    assert _decode(encoded) == "Movimientos de inventario"


def test_attachments_are_extracted_from_a_message() -> None:
    message = email.message.EmailMessage()
    message["Subject"] = "Reporte"
    message.set_content("va adjunto")
    message.add_attachment(
        b"col\nvalor\n", maintype="text", subtype="plain", filename="reporte.csv"
    )

    found = _attachments(message)

    assert [item.filename for item in found] == ["reporte.csv"]
    assert found[0].content == b"col\nvalor\n"


def test_saving_stamps_the_name_but_keeps_the_original(tmp_path) -> None:
    files = materialise(
        [Attachment(filename="inventario.xlsx", content=b"fake")], tmp_path, RECEIVED
    )

    (stored,) = files
    assert stored.original_name == "inventario.xlsx"
    assert stored.path.name == "20261020_063000__inventario.xlsx"


def test_zip_attachments_are_replaced_by_their_contents(tmp_path) -> None:
    archive = tmp_path / "bundle.zip"
    with zipfile.ZipFile(archive, "w") as bundle:
        bundle.writestr("ventas_sucursal_2026-10-20.txt", "FECHA|UNIDADES\n")

    files = materialise(
        [Attachment(filename="bundle.zip", content=archive.read_bytes())],
        tmp_path / "work",
        RECEIVED,
    )

    (extracted,) = files
    assert extracted.original_name == "ventas_sucursal_2026-10-20.txt"
    assert extracted.path.read_text(encoding="utf-8").startswith("FECHA|")


def test_a_zip_escaping_the_directory_is_refused(tmp_path) -> None:
    """A crafted archive must not be able to write outside the work folder."""
    archive = tmp_path / "evil.zip"
    with zipfile.ZipFile(archive, "w") as bundle:
        bundle.writestr("../../escaped.txt", "nope")

    work = tmp_path / "work"
    work.mkdir()
    local = LocalFile(path=archive, original_name="evil.zip")

    extracted = extract_zip(local, work, RECEIVED)

    # The entry is reduced to its base name, so it lands inside the work
    # directory and nothing appears above it.
    assert extracted[0].path.parent == work

    assert not (tmp_path.parent / "escaped.txt").exists()
    assert not (tmp_path / "escaped.txt").exists()


def test_an_unreadable_zip_returns_nothing(tmp_path) -> None:
    broken = tmp_path / "broken.zip"
    broken.write_bytes(b"not a zip at all")

    assert extract_zip(LocalFile(broken, "broken.zip"), tmp_path, RECEIVED) == []
