"""Writing attachments to disk and unpacking the zipped ones."""

from __future__ import annotations

import logging
import zipfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from .mailbox import Attachment

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class LocalFile:
    """An attachment on disk.

    The two names are deliberately separate: `path` is where it was stored,
    prefixed with a timestamp so two deliveries never overwrite each other,
    while `original_name` is what it was called in the email. Report patterns
    are matched against the original name, because the configuration describes
    the file as it arrives, not how this pipeline happens to store it.
    """

    path: Path
    original_name: str


def _stamped_name(filename: str, received_at: datetime) -> str:
    return f"{received_at:%Y%m%d_%H%M%S}__{Path(filename).name}"


def save(attachment: Attachment, work_dir: Path, received_at: datetime) -> LocalFile:
    work_dir.mkdir(parents=True, exist_ok=True)
    target = work_dir / _stamped_name(attachment.filename, received_at)
    target.write_bytes(attachment.content)
    logger.info("Saved %s", target.name)
    return LocalFile(path=target, original_name=Path(attachment.filename).name)


def extract_zip(archive: LocalFile, work_dir: Path, received_at: datetime) -> list[LocalFile]:
    """Unpack an archive, returning the files it contained.

    Every entry is reduced to its base name before being written. A crafted
    archive can carry a name like `../../evil`, and honouring it would let the
    archive overwrite files outside the work directory.
    """
    extracted: list[LocalFile] = []
    work_dir.mkdir(parents=True, exist_ok=True)

    try:
        with zipfile.ZipFile(archive.path) as bundle:
            for member in bundle.infolist():
                if member.is_dir():
                    continue
                member_name = Path(member.filename).name
                target = work_dir / _stamped_name(member_name, received_at)
                with bundle.open(member) as source:
                    target.write_bytes(source.read())
                extracted.append(LocalFile(path=target, original_name=member_name))
    except zipfile.BadZipFile as exc:
        logger.error("%s is not a readable zip: %s", archive.original_name, exc)
        return []

    if not extracted:
        logger.warning("%s contained no files", archive.original_name)
        return []

    logger.info("Extracted %d file(s) from %s", len(extracted), archive.original_name)
    archive.path.unlink(missing_ok=True)
    return extracted


def materialise(
    attachments: list[Attachment], work_dir: Path, received_at: datetime
) -> list[LocalFile]:
    """Save every attachment, replacing archives with their contents."""
    files: list[LocalFile] = []
    for attachment in attachments:
        saved = save(attachment, work_dir, received_at)
        if saved.path.suffix.lower() == ".zip":
            files.extend(extract_zip(saved, work_dir, received_at))
        else:
            files.append(saved)
    return files
