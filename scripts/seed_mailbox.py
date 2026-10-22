"""Deliver the synthetic reports to the test mailbox.

This is the stand-in for whatever system emails the reports every morning. It
sends one message per report per day, backdating the Date header so the
pipeline's incremental watermark has something real to work with.

    python scripts/seed_mailbox.py --days 3

Run it again with more days, or for a later date, and the next pipeline run
picks up only what it has not already loaded.
"""

from __future__ import annotations

import argparse
import os
import random
import smtplib
import sys
from datetime import date, datetime, time, timedelta
from email.message import EmailMessage
from email.utils import format_datetime
from pathlib import Path

from dotenv import load_dotenv
from faker import Faker

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from scripts.generate_data import build_attachments  # noqa: E402

load_dotenv(PROJECT_ROOT / ".env")

# Reports land in the inbox early, before anyone is at a desk.
DELIVERY_TIME = time(6, 30)


def send(host: str, port: int, recipient: str, subject: str,
         sent_at: datetime, files: list[tuple[str, bytes]]) -> None:
    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = "reportes@example.test"
    message["To"] = recipient
    message["Date"] = format_datetime(sent_at)
    message.set_content(
        f"Reporte automático del {sent_at:%d/%m/%Y}. El detalle va adjunto."
    )

    for filename, content in files:
        subtype = "zip" if filename.endswith(".zip") else "octet-stream"
        message.add_attachment(
            content, maintype="application", subtype=subtype, filename=filename
        )

    with smtplib.SMTP(host, port, timeout=20) as smtp:
        smtp.send_message(message)
    print(f"  sent {subject!r} ({sent_at:%Y-%m-%d}) with {len(files)} attachment(s)")


def main() -> None:
    parser = argparse.ArgumentParser(description="Seed the test mailbox.")
    parser.add_argument("--days", type=int, default=3, help="how many days to send")
    parser.add_argument(
        "--until",
        type=lambda value: datetime.strptime(value, "%Y-%m-%d").date(),
        default=date.today(),
        help="most recent report date to send (YYYY-MM-DD)",
    )
    parser.add_argument("--seed", type=int, default=42, help="seed for the content")
    args = parser.parse_args()

    host = os.getenv("SMTP_HOST", "localhost")
    port = int(os.getenv("SMTP_PORT", "3025"))
    recipient = os.getenv("IMAP_USER", "etl@example.test")

    random.seed(args.seed)
    Faker.seed(args.seed)

    print(f"Delivering {args.days} day(s) of reports to {recipient} via {host}:{port}")
    for offset in range(args.days - 1, -1, -1):
        report_date = args.until - timedelta(days=offset)
        sent_at = datetime.combine(report_date, DELIVERY_TIME)
        for subject, files in build_attachments(report_date).items():
            send(host, port, recipient, subject, sent_at, files)


if __name__ == "__main__":
    main()
