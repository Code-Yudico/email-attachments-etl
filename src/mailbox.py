"""Reading the mailbox over IMAP.

The original version of this pipeline drove Outlook through COM, which ties it
to one Windows desktop with Outlook installed and signed in. IMAP is the same
job through a standard protocol, so the pipeline runs anywhere -- including the
test mail server in `docker-compose.yml`.
"""

from __future__ import annotations

import email
import imaplib
import logging
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from email.header import decode_header, make_header
from email.message import Message
from email.utils import parsedate_to_datetime

from .config import MailboxConfig

logger = logging.getLogger(__name__)


class MailboxError(RuntimeError):
    """The mailbox could not be reached or read."""


@dataclass
class Attachment:
    filename: str
    content: bytes


@dataclass
class FetchedEmail:
    subject: str
    received_at: datetime
    attachments: list[Attachment] = field(default_factory=list)


def _decode(value: str | None) -> str:
    """Decode an RFC 2047 header such as '=?utf-8?q?Reporte?='."""
    if not value:
        return ""
    return str(make_header(decode_header(value)))


def _received_at(message: Message) -> datetime:
    """When the email arrived, always as naive UTC.

    Every return path is normalised on purpose. SQL Server's DATETIME2 carries
    no offset, and a single aware value reaching the watermark comparison would
    raise "can't compare offset-naive and offset-aware datetimes" -- including
    the fallback below, which is the easy one to get wrong.
    """
    parsed: datetime | None = None
    raw = message.get("Date")
    if raw:
        try:
            parsed = parsedate_to_datetime(raw)
        except (TypeError, ValueError):
            logger.warning("Unreadable Date header: %r", raw)

    if parsed is None:
        parsed = datetime.now(timezone.utc)

    if parsed.tzinfo is not None:
        parsed = parsed.astimezone(timezone.utc).replace(tzinfo=None)
    return parsed


def _attachments(message: Message) -> list[Attachment]:
    found: list[Attachment] = []
    for part in message.walk():
        if part.get_content_maintype() == "multipart":
            continue
        filename = _decode(part.get_filename())
        if not filename:
            continue
        payload = part.get_payload(decode=True)
        if payload is None:
            logger.warning("Attachment %s had no readable payload", filename)
            continue
        found.append(Attachment(filename=filename, content=payload))
    return found


@contextmanager
def open_mailbox(config: MailboxConfig):
    """Connect, select the folder, and always log out afterwards."""
    client_class = imaplib.IMAP4_SSL if config.use_ssl else imaplib.IMAP4
    try:
        client = client_class(config.host, config.port)
    except OSError as exc:
        raise MailboxError(f"Could not reach {config.host}:{config.port}: {exc}") from exc

    try:
        client.login(config.user, config.password)
        status, _ = client.select(config.folder)
        if status != "OK":
            raise MailboxError(f"Could not select folder {config.folder!r}")
        logger.info("Connected to %s as %s", config.host, config.user)
        yield client
    except imaplib.IMAP4.error as exc:
        raise MailboxError(f"IMAP error: {exc}") from exc
    finally:
        try:
            client.logout()
        except Exception:  # pragma: no cover - best effort on the way out
            pass


def search_by_subject(client, subject: str, since: datetime) -> list[bytes]:
    """Find messages with this exact subject, newer than `since`.

    IMAP's SINCE only has day resolution, so the date is widened by a day and
    the exact cut is applied in `fetch_since` against each message's own
    timestamp. Filtering on the server first keeps the fetch small.
    """
    date_token = (since - timedelta(days=1)).strftime("%d-%b-%Y")
    status, data = client.search(None, "SUBJECT", f'"{subject}"', "SINCE", date_token)
    if status != "OK":
        raise MailboxError(f"Search failed for subject {subject!r}")
    return data[0].split() if data and data[0] else []


def fetch_since(client, subject: str, since: datetime) -> list[FetchedEmail]:
    """Return the messages under `subject` received strictly after `since`.

    The watermark is exclusive so a rerun does not reprocess the last email
    already recorded in the control table.
    """
    results: list[FetchedEmail] = []

    for message_id in search_by_subject(client, subject, since):
        status, payload = client.fetch(message_id, "(RFC822)")
        if status != "OK" or not payload or not isinstance(payload[0], tuple):
            logger.warning("Could not fetch message %s", message_id)
            continue

        message = email.message_from_bytes(payload[0][1])
        received_at = _received_at(message)
        if received_at <= since:
            continue

        found = _attachments(message)
        if not found:
            logger.warning(
                "Email %r received %s had no attachments, skipping",
                _decode(message.get("Subject")),
                received_at,
            )
            continue

        results.append(
            FetchedEmail(
                subject=_decode(message.get("Subject")),
                received_at=received_at,
                attachments=found,
            )
        )

    # Oldest first, so the watermark only ever moves forward.
    results.sort(key=lambda item: item.received_at)
    logger.info("%s: %d new email(s)", subject, len(results))
    return results
