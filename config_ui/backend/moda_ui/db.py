"""SQLite connection helpers."""

from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
import sqlite3
from typing import Iterator

from .settings import get_settings


SCHEMA_PATH = Path(__file__).with_name("schema.sql")


def connect(path: Path | None = None) -> sqlite3.Connection:
    database_path = path or get_settings().database_path
    database_path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(database_path, timeout=10, check_same_thread=False)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute("PRAGMA journal_mode = WAL")
    return connection


def init_schema(connection: sqlite3.Connection) -> None:
    connection.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
    remove_cargo(connection)
    connection.commit()


def remove_cargo(connection: sqlite3.Connection) -> None:
    """Delete leftovers of the removed cargo (thùng hàng) analyzer."""
    connection.execute("DELETE FROM analyzer_lanes WHERE analyzer = 'cargo'")
    connection.execute("DELETE FROM analyzer_settings WHERE analyzer = 'cargo'")
    connection.execute(
        "DELETE FROM cameras WHERE purpose = 'cargo'"
        " AND id NOT IN (SELECT camera_id FROM site_sources)"
        " AND id NOT IN (SELECT camera_id FROM analyzer_lanes WHERE camera_id IS NOT NULL)"
    )


@contextmanager
def transaction(connection: sqlite3.Connection) -> Iterator[sqlite3.Connection]:
    try:
        yield connection
        connection.commit()
    except Exception:
        connection.rollback()
        raise


def get_db() -> Iterator[sqlite3.Connection]:
    """FastAPI dependency: one connection per request."""
    connection = connect()
    try:
        yield connection
    finally:
        connection.close()
