"""Configuration, read from environment variables.

Nothing here has a real default: `.env.example` documents every variable and
`.env`, which git ignores, holds the values.
"""

from __future__ import annotations

import os
import urllib.parse
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent.parent

load_dotenv(PROJECT_ROOT / ".env")


class ConfigError(RuntimeError):
    """A required setting is missing or malformed."""


def _required(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise ConfigError(
            f"Environment variable '{name}' is not set. "
            f"Copy .env.example to .env and fill it in."
        )
    return value


def _flag(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "y"}


def _int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError as exc:
        raise ConfigError(f"'{name}' must be an integer, got {raw!r}") from exc


@dataclass(frozen=True)
class MailboxConfig:
    host: str
    port: int
    user: str
    password: str
    folder: str
    use_ssl: bool

    def __repr__(self) -> str:  # pragma: no cover - diagnostics only
        return (
            f"MailboxConfig(host={self.host!r}, port={self.port}, "
            f"user={self.user!r}, password='***', folder={self.folder!r})"
        )


@dataclass(frozen=True)
class DatabaseConfig:
    server: str
    database: str
    user: str
    password: str
    driver: str
    trust_server_certificate: bool

    def odbc_string(self, database: str | None = None) -> str:
        parts = [
            f"DRIVER={{{self.driver}}}",
            f"SERVER={self.server}",
            f"DATABASE={database or self.database}",
            f"UID={self.user}",
            f"PWD={self.password}",
        ]
        if self.trust_server_certificate:
            parts.append("TrustServerCertificate=yes")
        return ";".join(parts) + ";"

    def sqlalchemy_url(self, database: str | None = None) -> str:
        """SQLAlchemy reaches SQL Server through the ODBC string above."""
        quoted = urllib.parse.quote_plus(self.odbc_string(database))
        return f"mssql+pyodbc:///?odbc_connect={quoted}"

    def __repr__(self) -> str:  # pragma: no cover - diagnostics only
        return (
            f"DatabaseConfig(server={self.server!r}, database={self.database!r}, "
            f"user={self.user!r}, password='***', driver={self.driver!r})"
        )


@dataclass(frozen=True)
class PipelineConfig:
    mailbox: MailboxConfig
    database: DatabaseConfig
    work_dir: Path
    initial_lookback_days: int


def load_config() -> PipelineConfig:
    mailbox = MailboxConfig(
        host=_required("IMAP_HOST"),
        port=_int("IMAP_PORT", 993),
        user=_required("IMAP_USER"),
        password=_required("IMAP_PASSWORD"),
        folder=os.getenv("IMAP_FOLDER", "INBOX"),
        use_ssl=_flag("IMAP_USE_SSL", default=True),
    )
    database = DatabaseConfig(
        server=_required("DB_SERVER"),
        database=_required("DB_DATABASE"),
        user=_required("DB_USER"),
        password=_required("DB_PASSWORD"),
        driver=os.getenv("DB_DRIVER", "ODBC Driver 18 for SQL Server"),
        trust_server_certificate=_flag("DB_TRUST_SERVER_CERTIFICATE"),
    )
    return PipelineConfig(
        mailbox=mailbox,
        database=database,
        work_dir=PROJECT_ROOT / os.getenv("WORK_DIR", "data/attachments"),
        initial_lookback_days=_int("INITIAL_LOOKBACK_DAYS", 30),
    )
