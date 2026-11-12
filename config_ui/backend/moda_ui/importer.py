"""Seed the database from the configuration files currently on disk."""

from __future__ import annotations

import json
import logging
from pathlib import Path
import sqlite3

from . import repository as repo
from .envfile import env_values_for_runtime
from .schemas import KAFKA_ENV, TRITON_ENV
from .security import encrypt_url
from .settings import Settings


logger = logging.getLogger(__name__)

WORKER_KEYS = (
    "group_id",
    "output_root",
    "worker_count",
    "event_timeout_seconds",
    "max_attempts",
    "retry_backoff_seconds",
    "cancellation_grace_seconds",
    "camera_open_timeout_ms",
    "camera_read_timeout_ms",
    "model_startup_attempts",
    "model_startup_delay_seconds",
)
WORKER_DEFAULTS = {
    "max_attempts": 2,
    "retry_backoff_seconds": 2,
    "cancellation_grace_seconds": 3,
    "camera_open_timeout_ms": 5000,
    "camera_read_timeout_ms": 5000,
    "model_startup_attempts": 10,
    "model_startup_delay_seconds": 2,
}


class ImportErrorDetail(ValueError):
    pass


def _env(settings: Settings, name: str) -> dict[str, str]:
    for candidate in (settings.project_dir / name, settings.project_dir / f"{name}.example"):
        if candidate.exists():
            return env_values_for_runtime(candidate.read_text(encoding="utf-8"))
    return {}


def _camera_code(env_name: str) -> str:
    code = env_name.lower()
    for prefix in ("moda_",):
        if code.startswith(prefix):
            code = code[len(prefix) :]
    for suffix in ("_camera_url", "_url"):
        if code.endswith(suffix):
            code = code[: -len(suffix)]
    return code


def _ensure_camera(
    conn: sqlite3.Connection, env_name: str, purpose: str, environment: dict[str, str], warnings: list[str]
) -> int:
    row = conn.execute("SELECT id FROM cameras WHERE env_name = ?", (env_name,)).fetchone()
    if row:
        return int(row["id"])
    url = environment.get(env_name, "")
    if not url:
        warnings.append(f"Không tìm thấy giá trị {env_name}; hãy nhập URL camera trên giao diện")
        url = "rtsp://chua-cau-hinh"
    code = _camera_code(env_name)
    cursor = conn.execute(
        "INSERT INTO cameras (code, name, purpose, env_name, rtsp_url_enc) VALUES (?, ?, ?, ?, ?)",
        (code, code.replace("_", " "), purpose, env_name, encrypt_url(url)),
    )
    return int(cursor.lastrowid)


def _ensure_plate_list(conn: sqlite3.Connection, path: Path) -> int:
    row = conn.execute(
        "SELECT id FROM reference_plate_lists WHERE file_name = ?", (path.name,)
    ).fetchone()
    if row:
        return int(row["id"])
    cursor = conn.execute(
        "INSERT INTO reference_plate_lists (name, file_name) VALUES (?, ?)", (path.stem, path.name)
    )
    list_id = int(cursor.lastrowid)
    if path.exists():
        plates = [
            line.strip().upper()
            for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        conn.executemany(
            "INSERT OR IGNORE INTO reference_plates (list_id, plate) VALUES (?, ?)",
            [(list_id, plate) for plate in plates],
        )
    return list_id


def _put(conn: sqlite3.Connection, table: str, analyzer: str | None, values: dict) -> None:
    for key, value in values.items():
        if analyzer is None:
            conn.execute(
                "INSERT OR REPLACE INTO system_settings (key, value) VALUES (?, ?)", (key, json.dumps(value))
            )
        else:
            conn.execute(
                "INSERT OR REPLACE INTO analyzer_settings (analyzer, key, value) VALUES (?, ?, ?)",
                (analyzer, key, json.dumps(value)),
            )


def import_from_files(conn: sqlite3.Connection, settings: Settings, *, force: bool = False) -> list[str]:
    """Returns warnings. Refuses to overwrite a populated database unless force=True."""
    if not repo.is_empty(conn) and not force:
        raise ImportErrorDetail("Database đã có dữ liệu; dùng --force để ghi đè")

    warnings: list[str] = []
    detector_env = _env(settings, settings.detector_env_file)
    analysis_env = _env(settings, settings.analysis_env_file)

    with conn:
        if force:
            for table in reversed(repo.SNAPSHOT_TABLES):
                conn.execute(f"DELETE FROM {table}")

        plate_dir = settings.config_dir / "reference_plates"
        for path in sorted(plate_dir.glob("*.txt")):
            _ensure_plate_list(conn, path)

        site_files = sorted((settings.config_dir / "sites").glob("*.json"))
        if not site_files:
            raise ImportErrorDetail(f"Không có file site nào trong {settings.config_dir / 'sites'}")
        for path in site_files:
            raw = json.loads(path.read_text(encoding="utf-8"))
            plate_path = (path.parent / raw["reference_plate_path"]).resolve()
            list_id = _ensure_plate_list(conn, plate_path)
            models = raw["models"]
            cursor = conn.execute(
                "INSERT INTO sites (code, name, vehicle_confidence, plate_character_confidence,"
                " plate_collection_seconds, video_fps, output_topic, output_partition,"
                " publish_timeout_seconds, vehicle_image_root, model_tracker, model_vehicle_detection,"
                " model_plate_detection, model_plate_recognition, reference_plate_list_id)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    path.stem,
                    raw["name"],
                    float(raw.get("vehicle_confidence", 0.8)),
                    float(raw.get("plate_character_confidence", 0.8)),
                    float(raw.get("plate_collection_seconds", 4.0)),
                    float(raw.get("video_fps", 10.0)),
                    raw.get("output_topic", "VehicleDetection_Gate1"),
                    int(raw.get("output_partition", 0)),
                    float(raw.get("publish_timeout_seconds", 30.0)),
                    raw.get("vehicle_image_root", "/data/vehicle"),
                    models["tracker"],
                    models["vehicle_detection"],
                    models["plate_detection"],
                    models["plate_recognition"],
                    list_id,
                ),
            )
            site_id = int(cursor.lastrowid)
            for index, env_name in enumerate(raw["camera_url_envs"]):
                camera_id = _ensure_camera(conn, env_name, "lane", detector_env, warnings)
                key = str(index)
                cursor = conn.execute(
                    "INSERT INTO site_sources (site_id, source_index, camera_id, trigger_code)"
                    " VALUES (?, ?, ?, ?)",
                    (site_id, index, camera_id, raw["trigger_codes"][key]),
                )
                source_id = int(cursor.lastrowid)
                for kind in ("capture", "inference"):
                    conn.execute(
                        "INSERT INTO zones (site_source_id, kind, points_json) VALUES (?, ?, ?)",
                        (source_id, kind, json.dumps(raw[f"{kind}_zones"][key])),
                    )

        analysis_path = settings.config_dir / "post_analysis.json"
        analysis = json.loads(analysis_path.read_text(encoding="utf-8"))
        kafka = analysis_env.get(KAFKA_ENV) or detector_env.get(KAFKA_ENV, "")
        if not kafka:
            warnings.append(f"Không tìm thấy {KAFKA_ENV}; hãy nhập trên màn hình Hệ thống")
        _put(
            conn,
            "system_settings",
            None,
            {
                "kafka_bootstrap_servers": kafka,
                "triton_url": analysis_env.get(TRITON_ENV, "http://triton:8000"),
                "input_topic": analysis["input_topic"],
                "output_topic": analysis["output_topic"],
                "dead_letter_topic": analysis.get("dead_letter_topic", "AnalysisFailure_Gate1"),
                "publish_timeout_seconds": analysis.get("publish_timeout_seconds", 30),
                "utc_offset_hours": analysis.get("utc_offset_hours", 7),
            },
        )

        weight = analysis["weight"]
        weight_camera = _ensure_camera(conn, weight["camera_url_env"], "weight", analysis_env, warnings)
        _put(
            conn,
            "analyzer_settings",
            "weight",
            {
                "camera_id": weight_camera,
                "model": weight["model"],
                **{key: weight.get(key, WORKER_DEFAULTS.get(key)) for key in WORKER_KEYS},
                "max_seconds": weight.get("max_seconds", 60),
                "confidence": weight.get("confidence", 0.5),
                "drop_threshold": weight.get("drop_threshold", 3000),
                "max_drop_count": weight.get("max_drop_count", 70),
            },
        )
        for trigger_code, crop in weight["crops"].items():
            conn.execute(
                "INSERT INTO analyzer_lanes (analyzer, trigger_code, analysis_code, crop_x_min,"
                " crop_x_max, crop_y_min, crop_y_max) VALUES ('weight', ?, ?, ?, ?, ?, ?)",
                (trigger_code, weight["analysis_codes"][trigger_code], *crop),
            )

        cargo = analysis["cargo"]
        _put(
            conn,
            "analyzer_settings",
            "cargo",
            {
                "detector_model": cargo["detector_model"],
                "classifier_model": cargo["classifier_model"],
                **{key: cargo.get(key, WORKER_DEFAULTS.get(key)) for key in WORKER_KEYS},
                "detection_confidence": cargo.get("detection_confidence", 0.8),
                "preferred_class_confidence": cargo.get("preferred_class_confidence", 0.7),
                "settle_seconds": cargo.get("settle_seconds", 5),
                "capture_retries": cargo.get("capture_retries", 3),
            },
        )
        for trigger_code, env_name in cargo["camera_url_envs"].items():
            camera_id = _ensure_camera(conn, env_name, "cargo", analysis_env, warnings)
            conn.execute(
                "INSERT INTO analyzer_lanes (analyzer, trigger_code, analysis_code, camera_id)"
                " VALUES ('cargo', ?, ?, ?)",
                (trigger_code, cargo["analysis_codes"][trigger_code], camera_id),
            )

    for warning in warnings:
        logger.warning(warning)
    return warnings
