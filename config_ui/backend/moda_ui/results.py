"""Recognition results store (separate SQLite file from the configuration DB).

Written by the `record` command (Kafka consumer), read by the /api/results endpoints.

Join keys between topics:
- VehicleDetection_Gate1 -> one row in vehicle_events, keyed by section_id (UUID).
- AnalysisResult_Gate1   -> copies the whole vehicle event, so it carries the same section_id.
- AnalysisFailure_Gate1  -> only knows the Kafka offset of the vehicle event (source_offset).
"""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
import sqlite3
from typing import Iterator, Mapping

from .settings import get_settings


SCHEMA = """
CREATE TABLE IF NOT EXISTS vehicle_events (
    id              INTEGER PRIMARY KEY,
    section_id      TEXT NOT NULL UNIQUE,
    kafka_partition INTEGER,
    kafka_offset    INTEGER,
    trigger_code    TEXT NOT NULL,
    trigger_cam_id  INTEGER,
    plate           TEXT,
    raw_plate       TEXT,
    best_match      TEXT,
    match_score     REAL,
    requires_search INTEGER NOT NULL DEFAULT 0,
    event_time      TEXT NOT NULL,
    frame_num       INTEGER,
    image_path      TEXT,
    received_at     TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_vehicle_events_time ON vehicle_events(event_time);
CREATE INDEX IF NOT EXISTS ix_vehicle_events_lane_time ON vehicle_events(trigger_code, event_time);
CREATE INDEX IF NOT EXISTS ix_vehicle_events_plate ON vehicle_events(plate);
CREATE INDEX IF NOT EXISTS ix_vehicle_events_offset ON vehicle_events(kafka_partition, kafka_offset);

CREATE TABLE IF NOT EXISTS analysis_results (
    id            INTEGER PRIMARY KEY,
    section_id    TEXT NOT NULL,
    analyzer      TEXT NOT NULL,
    analysis_code TEXT,
    output        TEXT,
    image_path    TEXT,
    received_at   TEXT NOT NULL,
    UNIQUE (section_id, analyzer)
);

CREATE TABLE IF NOT EXISTS analysis_failures (
    id               INTEGER PRIMARY KEY,
    service          TEXT NOT NULL,
    source_partition INTEGER,
    source_offset    INTEGER,
    trigger_code     TEXT,
    error            TEXT,
    failed_at        TEXT,
    UNIQUE (service, source_partition, source_offset)
);
"""

# analysis_cam_id values written by post_analysis (weight_reader.py).
ANALYZERS = {1: "weight"}
SERVICE_ANALYZER = {"weight_reader": "weight"}

# Container-side image roots (compose.yaml / compose.analysis.yaml) -> path under runtime/.
DETECTOR_IMAGE_ROOT = "/data/vehicle/"
ANALYSIS_IMAGE_ROOT = "/workspace/traffic/"


def connect(path: Path | None = None) -> sqlite3.Connection:
    database_path = path or get_settings().results_db_path
    database_path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(database_path, timeout=10, check_same_thread=False)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA journal_mode = WAL")
    connection.execute("PRAGMA synchronous = NORMAL")
    return connection


def init_schema(connection: sqlite3.Connection) -> None:
    connection.executescript(SCHEMA)
    connection.commit()


def get_results_db() -> Iterator[sqlite3.Connection]:
    """FastAPI dependency."""
    connection = connect()
    try:
        init_schema(connection)
        yield connection
    finally:
        connection.close()


@contextmanager
def transaction(connection: sqlite3.Connection) -> Iterator[sqlite3.Connection]:
    try:
        yield connection
        connection.commit()
    except Exception:
        connection.rollback()
        raise


# --- image paths -----------------------------------------------------------------------


def _safe_relative(path: str) -> str | None:
    parts = PurePosixPath(path).parts
    if not parts or any(part in ("..", "") for part in parts) or parts[0] == "/":
        return None
    return str(PurePosixPath(*parts))


def vehicle_image_relpath(container_path: str | None, trigger_code: str) -> str | None:
    """/data/vehicle/20261111/x.jpg of service <lane> -> vehicle/<lane>/20261111/x.jpg

    A gate container runs several lanes and each writes to /data/vehicle/<lane>/...
    (compose mounts runtime/vehicle/<lane> there): same result."""
    if not container_path:
        return None
    if container_path.startswith(DETECTOR_IMAGE_ROOT):
        rest = container_path[len(DETECTOR_IMAGE_ROOT):]
        lane = _safe_relative(trigger_code)
        if lane and rest.startswith(lane + "/"):
            rest = rest[len(lane) + 1:]
        rest = _safe_relative(rest)
        return f"vehicle/{lane}/{rest}" if rest and lane else None
    return _safe_relative(container_path)


def analysis_image_relpath(path: str | None) -> str | None:
    """weight: /workspace/traffic/weight_result/.."""
    if not path:
        return None
    if path.startswith(ANALYSIS_IMAGE_ROOT):
        path = path[len(ANALYSIS_IMAGE_ROOT):]
    return _safe_relative(path)


def resolve_image(relpath: str) -> Path | None:
    """Absolute host path under runtime/, or None if outside it or missing."""
    runtime = get_settings().runtime_dir.resolve()
    candidate = (runtime / relpath).resolve()
    if runtime not in candidate.parents or not candidate.is_file():
        return None
    return candidate


# --- ingest ----------------------------------------------------------------------------


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _float(value: object) -> float | None:
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def _text(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def record_vehicle(
    conn: sqlite3.Connection, event: Mapping[str, object], partition: int | None, offset: int | None
) -> bool:
    section_id = _text(event.get("section_id"))
    trigger_code = _text(event.get("trigger_code"))
    if not section_id or not trigger_code:
        return False
    conn.execute(
        """
        INSERT INTO vehicle_events (
            section_id, kafka_partition, kafka_offset, trigger_code, trigger_cam_id, plate,
            raw_plate, best_match, match_score, requires_search, event_time, frame_num,
            image_path, received_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(section_id) DO UPDATE SET
            kafka_partition = excluded.kafka_partition,
            kafka_offset = excluded.kafka_offset,
            plate = excluded.plate,
            raw_plate = excluded.raw_plate,
            best_match = excluded.best_match,
            match_score = excluded.match_score,
            requires_search = excluded.requires_search,
            image_path = excluded.image_path
        """,
        (
            section_id,
            partition,
            offset,
            trigger_code,
            event.get("trigger_cam_id"),
            _text(event.get("detected_object_id")),
            _text(event.get("raw_license_plate")),
            _text(event.get("best_match_lp")),
            _float(event.get("score_matching")),
            1 if event.get("search") else 0,
            _text(event.get("timestamp")) or _now(),
            event.get("frame_num"),
            vehicle_image_relpath(_text(event.get("trigger_image")), trigger_code),
            _now(),
        ),
    )
    return True


def record_analysis(conn: sqlite3.Connection, event: Mapping[str, object]) -> bool:
    section_id = _text(event.get("section_id"))
    analyzer = ANALYZERS.get(event.get("analysis_cam_id"))  # type: ignore[arg-type]
    if not section_id or not analyzer:
        return False
    # The result repeats the vehicle event: store it too in case the detection was missed.
    if not _vehicle_exists(conn, section_id):
        record_vehicle(conn, event, None, None)
    output = event.get("analysis_output")
    conn.execute(
        """
        INSERT INTO analysis_results (section_id, analyzer, analysis_code, output, image_path, received_at)
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(section_id, analyzer) DO UPDATE SET
            analysis_code = excluded.analysis_code,
            output = excluded.output,
            image_path = excluded.image_path,
            received_at = excluded.received_at
        """,
        (
            section_id,
            analyzer,
            _text(event.get("analysis_code")),
            None if output is None else str(output),
            analysis_image_relpath(_text(event.get("analysis_image"))),
            _now(),
        ),
    )
    return True


def _vehicle_exists(conn: sqlite3.Connection, section_id: str) -> bool:
    row = conn.execute("SELECT 1 FROM vehicle_events WHERE section_id = ?", (section_id,)).fetchone()
    return row is not None


def record_failure(conn: sqlite3.Connection, event: Mapping[str, object]) -> bool:
    service = _text(event.get("service"))
    if not service:
        return False
    error = _text(event.get("error")) or ""
    lines = [line for line in error.strip().splitlines() if line.strip()]
    conn.execute(
        """
        INSERT INTO analysis_failures (service, source_partition, source_offset, trigger_code, error, failed_at)
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(service, source_partition, source_offset) DO UPDATE SET
            error = excluded.error, failed_at = excluded.failed_at
        """,
        (
            service,
            event.get("source_partition"),
            event.get("source_offset"),
            _text(event.get("trigger_code")),
            lines[-1] if lines else error,
            _text(event.get("failed_at")),
        ),
    )
    return True


# --- queries ---------------------------------------------------------------------------


def _filters(
    *,
    lane: str | None,
    plate: str | None,
    date_from: str | None,
    date_to: str | None,
    unmatched: bool,
    no_plate: bool,
) -> tuple[str, list[object]]:
    clauses: list[str] = []
    params: list[object] = []
    if lane:
        clauses.append("v.trigger_code = ?")
        params.append(lane)
    if plate:
        like = f"%{plate.strip().upper().replace('-', '').replace('.', '').replace(' ', '')}%"
        clauses.append("(v.plate LIKE ? OR v.raw_plate LIKE ? OR v.best_match LIKE ?)")
        params.extend([like, like, like])
    if date_from:
        clauses.append("v.event_time >= ?")
        params.append(date_from)
    if date_to:
        clauses.append("v.event_time < ?")
        params.append(date_to)
    if unmatched:
        # Exact match only counts when the recognised plate equals the reference plate.
        clauses.append("(v.plate IS NULL OR v.best_match IS NULL OR v.plate <> v.best_match)")
    if no_plate:
        clauses.append("v.plate IS NULL")
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    return where, params


def list_events(
    conn: sqlite3.Connection,
    *,
    lane: str | None = None,
    plate: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    unmatched: bool = False,
    no_plate: bool = False,
    page: int = 1,
    page_size: int = 20,
) -> dict:
    where, params = _filters(
        lane=lane, plate=plate, date_from=date_from, date_to=date_to, unmatched=unmatched, no_plate=no_plate
    )
    total = conn.execute(f"SELECT COUNT(*) FROM vehicle_events v {where}", params).fetchone()[0]
    rows = conn.execute(
        f"""
        SELECT v.* FROM vehicle_events v {where}
        ORDER BY v.event_time DESC, v.id DESC
        LIMIT ? OFFSET ?
        """,
        [*params, page_size, (page - 1) * page_size],
    ).fetchall()
    items = [_event_dict(row) for row in rows]
    _attach_analysis(conn, items)
    return {"total": total, "page": page, "page_size": page_size, "items": items}


def _event_dict(row: sqlite3.Row) -> dict:
    data = dict(row)
    data["requires_search"] = bool(data["requires_search"])
    data["in_reference"] = bool(data["plate"]) and data["plate"] == data["best_match"]
    data["weight"] = None
    data["failures"] = []
    return data


def _attach_analysis(conn: sqlite3.Connection, items: list[dict]) -> None:
    if not items:
        return
    by_section = {item["section_id"]: item for item in items}
    marks = ",".join("?" for _ in by_section)
    for row in conn.execute(
        f"SELECT * FROM analysis_results WHERE section_id IN ({marks})", list(by_section)
    ):
        if row["analyzer"] not in ANALYZERS.values():
            continue
        by_section[row["section_id"]][row["analyzer"]] = {
            "output": row["output"],
            "analysis_code": row["analysis_code"],
            "image_path": row["image_path"],
            "received_at": row["received_at"],
        }
    by_offset = {
        (item["kafka_partition"], item["kafka_offset"]): item
        for item in items
        if item["kafka_offset"] is not None
    }
    if not by_offset:
        return
    offsets = sorted({offset for _, offset in by_offset})
    marks = ",".join("?" for _ in offsets)
    for row in conn.execute(
        f"SELECT * FROM analysis_failures WHERE source_offset IN ({marks})", offsets
    ):
        item = by_offset.get((row["source_partition"], row["source_offset"]))
        if item is None:
            continue
        analyzer = SERVICE_ANALYZER.get(row["service"])
        if analyzer is None:
            continue
        if item.get(analyzer):
            continue  # a later retry succeeded
        item["failures"].append({"analyzer": analyzer, "error": row["error"], "failed_at": row["failed_at"]})


def get_event(conn: sqlite3.Connection, event_id: int) -> dict | None:
    row = conn.execute("SELECT * FROM vehicle_events WHERE id = ?", (event_id,)).fetchone()
    if row is None:
        return None
    items = [_event_dict(row)]
    _attach_analysis(conn, items)
    return items[0]


def stats(conn: sqlite3.Connection, *, date_from: str | None, date_to: str | None) -> dict:
    where, params = _filters(
        lane=None, plate=None, date_from=date_from, date_to=date_to, unmatched=False, no_plate=False
    )
    row = conn.execute(
        f"""
        SELECT COUNT(*) AS total,
               SUM(CASE WHEN v.plate IS NULL THEN 1 ELSE 0 END) AS no_plate,
               SUM(CASE WHEN v.plate IS NOT NULL AND v.plate = v.best_match THEN 1 ELSE 0 END) AS in_reference,
               MAX(v.event_time) AS last_event
        FROM vehicle_events v {where}
        """,
        params,
    ).fetchone()
    lanes = conn.execute(
        f"SELECT v.trigger_code AS lane, COUNT(*) AS count FROM vehicle_events v {where} "
        "GROUP BY v.trigger_code ORDER BY v.trigger_code",
        params,
    ).fetchall()
    total = row["total"] or 0
    return {
        "total": total,
        "no_plate": row["no_plate"] or 0,
        "in_reference": row["in_reference"] or 0,
        "out_of_reference": total - (row["in_reference"] or 0),
        "last_event": row["last_event"],
        "lanes": [dict(item) for item in lanes],
    }


def lanes(conn: sqlite3.Connection) -> list[str]:
    return [row[0] for row in conn.execute("SELECT DISTINCT trigger_code FROM vehicle_events ORDER BY 1")]
