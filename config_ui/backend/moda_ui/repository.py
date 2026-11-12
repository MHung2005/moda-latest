"""Data access for every configuration table."""

from __future__ import annotations

import json
import sqlite3
from typing import Any

from . import schemas
from .security import decrypt_url, encrypt_url, mask_url


class NotFound(LookupError):
    pass


class Conflict(ValueError):
    pass


def _row(row: sqlite3.Row | None, what: str) -> dict:
    if row is None:
        raise NotFound(what)
    return dict(row)


# --- users ---------------------------------------------------------------------------


def list_users(conn: sqlite3.Connection) -> list[dict]:
    rows = conn.execute("SELECT id, username, role, created_at FROM users ORDER BY id")
    return [dict(row) for row in rows]


def get_user_by_name(conn: sqlite3.Connection, username: str) -> dict | None:
    row = conn.execute("SELECT * FROM users WHERE username = ?", (username,)).fetchone()
    return dict(row) if row else None


def get_user(conn: sqlite3.Connection, user_id: int) -> dict:
    row = conn.execute(
        "SELECT id, username, role FROM users WHERE id = ?", (user_id,)
    ).fetchone()
    return _row(row, f"user {user_id}")


def create_user(conn: sqlite3.Connection, username: str, password_hash: str, role: str) -> int:
    try:
        cursor = conn.execute(
            "INSERT INTO users (username, password_hash, role) VALUES (?, ?, ?)",
            (username, password_hash, role),
        )
    except sqlite3.IntegrityError as error:
        raise Conflict(f"Tài khoản {username} đã tồn tại") from error
    conn.commit()
    return int(cursor.lastrowid)


def set_user_password(conn: sqlite3.Connection, user_id: int, password_hash: str) -> None:
    conn.execute("UPDATE users SET password_hash = ? WHERE id = ?", (password_hash, user_id))
    conn.commit()


def delete_user(conn: sqlite3.Connection, user_id: int) -> None:
    admins = conn.execute("SELECT id FROM users WHERE role = 'admin'").fetchall()
    if [row["id"] for row in admins] == [user_id]:
        raise Conflict("Không thể xoá admin cuối cùng")
    conn.execute("UPDATE config_revisions SET user_id = NULL WHERE user_id = ?", (user_id,))
    conn.execute("DELETE FROM users WHERE id = ?", (user_id,))
    conn.commit()


# --- cameras -------------------------------------------------------------------------


def _camera_out(row: dict) -> dict:
    try:
        masked = mask_url(decrypt_url(row["rtsp_url_enc"]))
    except Exception:  # noqa: BLE001 - shown in UI, never fatal for listing
        masked = "<không giải mã được>"
    return {
        "id": row["id"],
        "code": row["code"],
        "name": row["name"],
        "purpose": row["purpose"],
        "env_name": row["env_name"],
        "rtsp_url_masked": masked,
        "frame_width": row["frame_width"],
        "frame_height": row["frame_height"],
        "updated_at": row["updated_at"],
    }


def list_cameras(conn: sqlite3.Connection) -> list[dict]:
    rows = conn.execute("SELECT * FROM cameras ORDER BY purpose, code").fetchall()
    return [_camera_out(dict(row)) for row in rows]


def get_camera_row(conn: sqlite3.Connection, camera_id: int) -> dict:
    row = conn.execute("SELECT * FROM cameras WHERE id = ?", (camera_id,)).fetchone()
    return _row(row, f"camera {camera_id}")


def get_camera(conn: sqlite3.Connection, camera_id: int) -> dict:
    return _camera_out(get_camera_row(conn, camera_id))


def get_camera_url(conn: sqlite3.Connection, camera_id: int) -> str:
    return decrypt_url(get_camera_row(conn, camera_id)["rtsp_url_enc"])


def create_camera(conn: sqlite3.Connection, data: schemas.CameraIn) -> int:
    if not data.rtsp_url:
        raise Conflict("Cần nhập URL RTSP cho camera mới")
    try:
        cursor = conn.execute(
            "INSERT INTO cameras (code, name, purpose, env_name, rtsp_url_enc)"
            " VALUES (?, ?, ?, ?, ?)",
            (data.code, data.name, data.purpose, data.env_name, encrypt_url(data.rtsp_url)),
        )
    except sqlite3.IntegrityError as error:
        raise Conflict("Mã camera hoặc tên biến môi trường đã tồn tại") from error
    conn.commit()
    return int(cursor.lastrowid)


def update_camera(conn: sqlite3.Connection, camera_id: int, data: schemas.CameraIn) -> None:
    current = get_camera_row(conn, camera_id)
    if current["purpose"] != data.purpose and _camera_in_use(conn, camera_id):
        raise Conflict("Camera đang được dùng, không thể đổi mục đích sử dụng")
    url_enc = encrypt_url(data.rtsp_url) if data.rtsp_url else current["rtsp_url_enc"]
    try:
        conn.execute(
            "UPDATE cameras SET code = ?, name = ?, purpose = ?, env_name = ?,"
            " rtsp_url_enc = ?, updated_at = datetime('now') WHERE id = ?",
            (data.code, data.name, data.purpose, data.env_name, url_enc, camera_id),
        )
    except sqlite3.IntegrityError as error:
        raise Conflict("Mã camera hoặc tên biến môi trường đã tồn tại") from error
    conn.commit()


def set_camera_resolution(conn: sqlite3.Connection, camera_id: int, width: int, height: int) -> None:
    conn.execute(
        "UPDATE cameras SET frame_width = ?, frame_height = ? WHERE id = ?",
        (width, height, camera_id),
    )
    conn.commit()


def _camera_in_use(conn: sqlite3.Connection, camera_id: int) -> bool:
    if conn.execute("SELECT 1 FROM site_sources WHERE camera_id = ?", (camera_id,)).fetchone():
        return True
    if conn.execute("SELECT 1 FROM analyzer_lanes WHERE camera_id = ?", (camera_id,)).fetchone():
        return True
    weight = get_analyzer_settings(conn, "weight")
    return weight.get("camera_id") == camera_id


def delete_camera(conn: sqlite3.Connection, camera_id: int) -> None:
    get_camera_row(conn, camera_id)
    if _camera_in_use(conn, camera_id):
        raise Conflict("Camera đang được làn xe hoặc analyzer sử dụng")
    conn.execute("DELETE FROM cameras WHERE id = ?", (camera_id,))
    conn.commit()


# --- reference plates ----------------------------------------------------------------


def list_plate_lists(conn: sqlite3.Connection) -> list[dict]:
    rows = conn.execute(
        "SELECT l.id, l.name, l.file_name, COUNT(p.plate) AS plate_count"
        " FROM reference_plate_lists l LEFT JOIN reference_plates p ON p.list_id = l.id"
        " GROUP BY l.id ORDER BY l.name"
    )
    return [dict(row) for row in rows]


def create_plate_list(conn: sqlite3.Connection, name: str, plates: list[str] | None = None) -> int:
    try:
        cursor = conn.execute(
            "INSERT INTO reference_plate_lists (name, file_name) VALUES (?, ?)",
            (name, f"{name}.txt"),
        )
    except sqlite3.IntegrityError as error:
        raise Conflict(f"Danh sách {name} đã tồn tại") from error
    list_id = int(cursor.lastrowid)
    if plates:
        conn.executemany(
            "INSERT OR IGNORE INTO reference_plates (list_id, plate) VALUES (?, ?)",
            [(list_id, plate) for plate in plates],
        )
    conn.commit()
    return list_id


def get_plates(conn: sqlite3.Connection, list_id: int) -> list[str]:
    _row(
        conn.execute("SELECT id FROM reference_plate_lists WHERE id = ?", (list_id,)).fetchone(),
        f"plate list {list_id}",
    )
    rows = conn.execute(
        "SELECT plate FROM reference_plates WHERE list_id = ? ORDER BY rowid", (list_id,)
    )
    return [row["plate"] for row in rows]


def replace_plates(conn: sqlite3.Connection, list_id: int, plates: list[str]) -> None:
    get_plates(conn, list_id)
    conn.execute("DELETE FROM reference_plates WHERE list_id = ?", (list_id,))
    conn.executemany(
        "INSERT OR IGNORE INTO reference_plates (list_id, plate) VALUES (?, ?)",
        [(list_id, plate) for plate in plates],
    )
    conn.commit()


def delete_plate_list(conn: sqlite3.Connection, list_id: int) -> None:
    if conn.execute(
        "SELECT 1 FROM sites WHERE reference_plate_list_id = ?", (list_id,)
    ).fetchone():
        raise Conflict("Danh sách đang được làn xe sử dụng")
    conn.execute("DELETE FROM reference_plate_lists WHERE id = ?", (list_id,))
    conn.commit()


# --- sites ---------------------------------------------------------------------------


SITE_FIELDS = (
    "name",
    "vehicle_confidence",
    "plate_character_confidence",
    "plate_collection_seconds",
    "video_fps",
    "output_topic",
    "output_partition",
    "publish_timeout_seconds",
    "model_tracker",
    "model_vehicle_detection",
    "model_plate_detection",
    "model_plate_recognition",
    "reference_plate_list_id",
)


def list_sites(conn: sqlite3.Connection) -> list[dict]:
    return [get_site(conn, row["code"]) for row in conn.execute("SELECT code FROM sites ORDER BY code")]


def get_site(conn: sqlite3.Connection, code: str) -> dict:
    site = _row(conn.execute("SELECT * FROM sites WHERE code = ?", (code,)).fetchone(), f"site {code}")
    sources = []
    for source in conn.execute(
        "SELECT * FROM site_sources WHERE site_id = ? ORDER BY source_index", (site["id"],)
    ).fetchall():
        zones = {
            row["kind"]: json.loads(row["points_json"])
            for row in conn.execute(
                "SELECT kind, points_json FROM zones WHERE site_source_id = ?", (source["id"],)
            )
        }
        sources.append(
            {
                "source_index": source["source_index"],
                "camera_id": source["camera_id"],
                "trigger_code": source["trigger_code"],
                "capture_zone": zones.get("capture", []),
                "inference_zone": zones.get("inference", []),
            }
        )
    site["sources"] = sources
    return site


def update_site(conn: sqlite3.Connection, code: str, data: schemas.SiteIn) -> None:
    get_site(conn, code)
    values = data.model_dump()
    assignments = ", ".join(f"{field} = ?" for field in SITE_FIELDS)
    try:
        conn.execute(
            f"UPDATE sites SET {assignments}, updated_at = datetime('now') WHERE code = ?",
            (*[values[field] for field in SITE_FIELDS], code),
        )
    except sqlite3.IntegrityError as error:
        raise Conflict("Danh sách biển số không tồn tại") from error
    conn.commit()


def _source_id(conn: sqlite3.Connection, code: str, index: int) -> int:
    row = conn.execute(
        "SELECT s.id FROM site_sources s JOIN sites t ON t.id = s.site_id"
        " WHERE t.code = ? AND s.source_index = ?",
        (code, index),
    ).fetchone()
    return int(_row(row, f"source {index} of {code}")["id"])


def upsert_source(conn: sqlite3.Connection, code: str, index: int, data: schemas.SourceIn) -> None:
    site = get_site(conn, code)
    camera = get_camera_row(conn, data.camera_id)
    if camera["purpose"] != "lane":
        raise Conflict("Nguồn của làn xe phải là camera loại 'lane'")
    existing = {source["source_index"] for source in site["sources"]}
    if index not in existing and index != len(existing):
        raise Conflict(f"Nguồn tiếp theo phải có số thứ tự {len(existing)}")
    conn.execute(
        "INSERT INTO site_sources (site_id, source_index, camera_id, trigger_code)"
        " VALUES (?, ?, ?, ?) ON CONFLICT (site_id, source_index)"
        " DO UPDATE SET camera_id = excluded.camera_id, trigger_code = excluded.trigger_code",
        (site["id"], index, data.camera_id, data.trigger_code),
    )
    conn.execute("UPDATE sites SET updated_at = datetime('now') WHERE id = ?", (site["id"],))
    conn.commit()


def delete_source(conn: sqlite3.Connection, code: str, index: int) -> None:
    site = get_site(conn, code)
    indexes = [source["source_index"] for source in site["sources"]]
    if len(indexes) <= 1:
        raise Conflict("Làn xe phải có ít nhất một nguồn camera")
    if index != max(indexes):
        raise Conflict("Chỉ xoá được nguồn có số thứ tự lớn nhất")
    conn.execute("DELETE FROM site_sources WHERE id = ?", (_source_id(conn, code, index),))
    conn.commit()


def set_zone(conn: sqlite3.Connection, code: str, index: int, kind: str, points: list) -> None:
    source_id = _source_id(conn, code, index)
    conn.execute(
        "INSERT INTO zones (site_source_id, kind, points_json) VALUES (?, ?, ?)"
        " ON CONFLICT (site_source_id, kind) DO UPDATE SET points_json = excluded.points_json",
        (source_id, kind, json.dumps([list(point) for point in points])),
    )
    conn.execute(
        "UPDATE sites SET updated_at = datetime('now') WHERE code = ?", (code,)
    )
    conn.commit()


# --- key/value settings --------------------------------------------------------------


def get_system_settings(conn: sqlite3.Connection) -> dict[str, Any]:
    return {row["key"]: json.loads(row["value"]) for row in conn.execute("SELECT * FROM system_settings")}


def set_system_settings(conn: sqlite3.Connection, values: dict[str, Any]) -> None:
    conn.executemany(
        "INSERT INTO system_settings (key, value) VALUES (?, ?)"
        " ON CONFLICT (key) DO UPDATE SET value = excluded.value",
        [(key, json.dumps(value)) for key, value in values.items()],
    )
    conn.commit()


def get_analyzer_settings(conn: sqlite3.Connection, analyzer: str) -> dict[str, Any]:
    rows = conn.execute("SELECT key, value FROM analyzer_settings WHERE analyzer = ?", (analyzer,))
    return {row["key"]: json.loads(row["value"]) for row in rows}


def set_analyzer_settings(conn: sqlite3.Connection, analyzer: str, values: dict[str, Any]) -> None:
    if analyzer == "weight":
        camera = get_camera_row(conn, int(values["camera_id"]))
        if camera["purpose"] != "weight":
            raise Conflict("Camera đọc cân phải là camera loại 'weight'")
    conn.executemany(
        "INSERT INTO analyzer_settings (analyzer, key, value) VALUES (?, ?, ?)"
        " ON CONFLICT (analyzer, key) DO UPDATE SET value = excluded.value",
        [(analyzer, key, json.dumps(value)) for key, value in values.items()],
    )
    conn.commit()


def list_analyzer_lanes(conn: sqlite3.Connection, analyzer: str) -> list[dict]:
    rows = conn.execute(
        "SELECT * FROM analyzer_lanes WHERE analyzer = ? ORDER BY rowid", (analyzer,)
    )
    return [dict(row) for row in rows]


def upsert_weight_lane(conn: sqlite3.Connection, trigger_code: str, data: schemas.WeightLaneIn) -> None:
    conn.execute(
        "INSERT INTO analyzer_lanes (analyzer, trigger_code, analysis_code,"
        " crop_x_min, crop_x_max, crop_y_min, crop_y_max) VALUES ('weight', ?, ?, ?, ?, ?, ?)"
        " ON CONFLICT (analyzer, trigger_code) DO UPDATE SET analysis_code = excluded.analysis_code,"
        " crop_x_min = excluded.crop_x_min, crop_x_max = excluded.crop_x_max,"
        " crop_y_min = excluded.crop_y_min, crop_y_max = excluded.crop_y_max",
        (trigger_code, data.analysis_code, data.crop_x_min, data.crop_x_max, data.crop_y_min, data.crop_y_max),
    )
    conn.commit()


def delete_analyzer_lane(conn: sqlite3.Connection, analyzer: str, trigger_code: str) -> None:
    conn.execute(
        "DELETE FROM analyzer_lanes WHERE analyzer = ? AND trigger_code = ?", (analyzer, trigger_code)
    )
    conn.commit()


# --- snapshots (revision history) ----------------------------------------------------

SNAPSHOT_TABLES = (
    # Insert order; deletion runs in reverse.
    "cameras",
    "reference_plate_lists",
    "reference_plates",
    "sites",
    "site_sources",
    "zones",
    "system_settings",
    "analyzer_settings",
    "analyzer_lanes",
)


def export_snapshot(conn: sqlite3.Connection) -> dict[str, list[dict]]:
    return {
        table: [dict(row) for row in conn.execute(f"SELECT * FROM {table}")]
        for table in SNAPSHOT_TABLES
    }


def import_snapshot(conn: sqlite3.Connection, snapshot: dict[str, list[dict]]) -> None:
    with conn:
        for table in reversed(SNAPSHOT_TABLES):
            conn.execute(f"DELETE FROM {table}")
        for table in SNAPSHOT_TABLES:
            for row in snapshot.get(table, []):
                columns = ", ".join(row)
                placeholders = ", ".join("?" for _ in row)
                conn.execute(
                    f"INSERT INTO {table} ({columns}) VALUES ({placeholders})", tuple(row.values())
                )


def is_empty(conn: sqlite3.Connection) -> bool:
    return conn.execute("SELECT COUNT(*) FROM sites").fetchone()[0] == 0


# --- revisions -----------------------------------------------------------------------


def create_revision(
    conn: sqlite3.Connection,
    *,
    user_id: int | None,
    note: str,
    snapshot: dict,
    files_diff: str,
    services: list[str],
    status: str,
    log: str,
) -> int:
    cursor = conn.execute(
        "INSERT INTO config_revisions (user_id, note, snapshot_json, files_diff, services, status, log)"
        " VALUES (?, ?, ?, ?, ?, ?, ?)",
        (user_id, note, json.dumps(snapshot), files_diff, json.dumps(services), status, log),
    )
    conn.commit()
    return int(cursor.lastrowid)


def list_revisions(conn: sqlite3.Connection, limit: int = 100) -> list[dict]:
    rows = conn.execute(
        "SELECT r.id, r.created_at, r.note, r.services, r.status, u.username"
        " FROM config_revisions r LEFT JOIN users u ON u.id = r.user_id"
        " ORDER BY r.id DESC LIMIT ?",
        (limit,),
    )
    return [{**dict(row), "services": json.loads(row["services"])} for row in rows]


def get_revision(conn: sqlite3.Connection, revision_id: int) -> dict:
    row = conn.execute(
        "SELECT r.*, u.username FROM config_revisions r LEFT JOIN users u ON u.id = r.user_id"
        " WHERE r.id = ?",
        (revision_id,),
    ).fetchone()
    revision = _row(row, f"revision {revision_id}")
    revision["services"] = json.loads(revision["services"])
    revision["snapshot"] = json.loads(revision.pop("snapshot_json"))
    return revision


def mark_rolled_back_after(conn: sqlite3.Connection, revision_id: int) -> None:
    conn.execute(
        "UPDATE config_revisions SET status = 'rolled_back' WHERE id > ? AND status = 'applied'",
        (revision_id,),
    )
    conn.commit()
