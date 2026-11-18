"""Turn the database into the exact files the runtime reads.

Output is a mapping {path relative to the project dir: file content}. Nothing is
written here; applier.py decides when and how files reach the disk."""

from __future__ import annotations

import json
from pathlib import Path
import sqlite3
from typing import Any

from . import repository as repo
from .envfile import update_env_text
from .schemas import KAFKA_ENV, TRITON_ENV, SystemSettings, WeightSettings
from .settings import Settings


class GenerationError(ValueError):
    """The database does not hold a complete configuration yet."""


SITE_DIR = "config/sites"
PLATE_DIR = "config/reference_plates"
POST_ANALYSIS_FILE = "config/post_analysis.json"


# --- JSON formatting that keeps point lists on one line, like the hand-written files --


def _is_flat(value: list) -> bool:
    """A list of scalars, or a list of lists of scalars (polygons)."""
    for item in value:
        if isinstance(item, dict):
            return False
        if isinstance(item, list) and any(isinstance(sub, (dict, list)) for sub in item):
            return False
    return True


def dump_json(value: Any, indent: int = 0) -> str:
    pad = "  " * indent
    inner = "  " * (indent + 1)
    if isinstance(value, dict):
        if not value:
            return "{}"
        if all(not isinstance(item, (dict, list)) for item in value.values()) and len(value) <= 2:
            return json.dumps(value, ensure_ascii=False)
        items = [
            f"{inner}{json.dumps(key, ensure_ascii=False)}: {dump_json(item, indent + 1)}"
            for key, item in value.items()
        ]
        return "{\n" + ",\n".join(items) + "\n" + pad + "}"
    if isinstance(value, list) and _is_flat(value):
        return json.dumps(value, ensure_ascii=False, separators=(", ", ": "))
    if isinstance(value, list):
        items = [f"{inner}{dump_json(item, indent + 1)}" for item in value]
        return "[\n" + ",\n".join(items) + "\n" + pad + "]"
    return json.dumps(value, ensure_ascii=False)


def _number(value: float) -> float | int:
    """Keep 30 as 30 and 0.7 as 0.7 in generated JSON."""
    if isinstance(value, float) and value.is_integer():
        return int(value)
    return value


# --- builders ------------------------------------------------------------------------


def _cameras(conn: sqlite3.Connection) -> dict[int, dict]:
    return {
        row["id"]: dict(row) for row in conn.execute("SELECT * FROM cameras").fetchall()
    }


def build_site(
    conn: sqlite3.Connection,
    site: dict,
    cameras: dict[int, dict],
    plate_files: dict[int, str],
    utc_offset_hours: int,
) -> dict:
    sources = site["sources"]
    if not sources:
        raise GenerationError(f"Làn {site['code']} chưa có nguồn camera")
    for source in sources:
        if len(source["capture_zone"]) < 3 or len(source["inference_zone"]) < 3:
            raise GenerationError(
                f"Làn {site['code']} nguồn {source['source_index']} chưa vẽ đủ vùng capture/inference"
            )
    return {
        "name": site["name"],
        "camera_url_envs": [cameras[source["camera_id"]]["env_name"] for source in sources],
        "trigger_codes": {str(s["source_index"]): s["trigger_code"] for s in sources},
        "capture_zones": {str(s["source_index"]): s["capture_zone"] for s in sources},
        "inference_zones": {str(s["source_index"]): s["inference_zone"] for s in sources},
        "models": {
            "tracker": site["model_tracker"],
            "vehicle_detection": site["model_vehicle_detection"],
            "plate_detection": site["model_plate_detection"],
            "plate_recognition": site["model_plate_recognition"],
        },
        "reference_plate_path": f"../reference_plates/{plate_files[site['reference_plate_list_id']]}",
        # One time zone for the whole deployment: system_settings.utc_offset_hours.
        "utc_offset_hours": utc_offset_hours,
        "vehicle_confidence": _number(site["vehicle_confidence"]),
        "plate_character_confidence": _number(site["plate_character_confidence"]),
        "plate_collection_seconds": float(site["plate_collection_seconds"]),
        "video_fps": float(site["video_fps"]),
        "output_topic": site["output_topic"],
        "output_partition": int(site["output_partition"]),
        "publish_timeout_seconds": _number(site["publish_timeout_seconds"]),
        "vehicle_image_root": site["vehicle_image_root"],
    }


def _worker_section(settings: dict) -> dict:
    return {
        "group_id": settings["group_id"],
        "output_root": settings["output_root"],
        "worker_count": settings["worker_count"],
        "event_timeout_seconds": _number(settings["event_timeout_seconds"]),
        "max_attempts": settings["max_attempts"],
        "retry_backoff_seconds": _number(settings["retry_backoff_seconds"]),
        "cancellation_grace_seconds": _number(settings["cancellation_grace_seconds"]),
        "camera_open_timeout_ms": settings["camera_open_timeout_ms"],
        "camera_read_timeout_ms": settings["camera_read_timeout_ms"],
        "model_startup_attempts": settings["model_startup_attempts"],
        "model_startup_delay_seconds": _number(settings["model_startup_delay_seconds"]),
    }


def _load_settings(conn: sqlite3.Connection, model, analyzer: str | None):
    raw = repo.get_system_settings(conn) if analyzer is None else repo.get_analyzer_settings(conn, analyzer)
    try:
        return model.model_validate(raw).model_dump()
    except Exception as error:  # pydantic.ValidationError
        name = "hệ thống" if analyzer is None else analyzer
        raise GenerationError(f"Cấu hình {name} chưa đầy đủ: {error}") from error


def build_post_analysis(conn: sqlite3.Connection, cameras: dict[int, dict]) -> dict:
    system = _load_settings(conn, SystemSettings, None)
    weight = _load_settings(conn, WeightSettings, "weight")
    weight_lanes = repo.list_analyzer_lanes(conn, "weight")
    if weight["camera_id"] not in cameras:
        raise GenerationError("Camera đọc cân không tồn tại")

    weight_section = {
        "camera_url_env": cameras[weight["camera_id"]]["env_name"],
        "model": weight["model"],
        **_worker_section(weight),
        "max_seconds": _number(weight["max_seconds"]),
        "confidence": _number(weight["confidence"]),
        "drop_threshold": weight["drop_threshold"],
        "max_drop_count": weight["max_drop_count"],
        "crops": {
            lane["trigger_code"]: [lane["crop_x_min"], lane["crop_x_max"], lane["crop_y_min"], lane["crop_y_max"]]
            for lane in weight_lanes
        },
        "analysis_codes": {lane["trigger_code"]: lane["analysis_code"] for lane in weight_lanes},
    }
    return {
        "kafka_bootstrap_servers_env": KAFKA_ENV,
        "triton_url_env": TRITON_ENV,
        "input_topic": system["input_topic"],
        "output_topic": system["output_topic"],
        "dead_letter_topic": system["dead_letter_topic"],
        "publish_timeout_seconds": _number(system["publish_timeout_seconds"]),
        "utc_offset_hours": system["utc_offset_hours"],
        "weight": weight_section,
    }


def env_updates(conn: sqlite3.Connection) -> tuple[dict[str, str], dict[str, str]]:
    """Variables managed by the UI: (detector .env, analysis .env.jetson)."""
    from .security import decrypt_url

    system = _load_settings(conn, SystemSettings, None)
    detector = {KAFKA_ENV: system["kafka_bootstrap_servers"]}
    analysis = {KAFKA_ENV: system["kafka_bootstrap_servers"], TRITON_ENV: system["triton_url"]}
    for camera in conn.execute("SELECT * FROM cameras ORDER BY code").fetchall():
        target = detector if camera["purpose"] == "lane" else analysis
        target[camera["env_name"]] = decrypt_url(camera["rtsp_url_enc"])
    return detector, analysis


def _base_env_text(project_dir: Path, name: str) -> str:
    for candidate in (project_dir / name, project_dir / f"{name}.example"):
        if candidate.exists():
            return candidate.read_text(encoding="utf-8")
    return ""


def build_files(conn: sqlite3.Connection, settings: Settings) -> dict[str, str]:
    cameras = _cameras(conn)
    plate_lists = repo.list_plate_lists(conn)
    plate_files = {item["id"]: item["file_name"] for item in plate_lists}
    files: dict[str, str] = {}
    utc_offset_hours = _load_settings(conn, SystemSettings, None)["utc_offset_hours"]

    for site in repo.list_sites(conn):
        content = build_site(conn, site, cameras, plate_files, utc_offset_hours)
        files[f"{SITE_DIR}/{site['code']}.json"] = dump_json(content) + "\n"

    for item in plate_lists:
        plates = repo.get_plates(conn, item["id"])
        files[f"{PLATE_DIR}/{item['file_name']}"] = "".join(f"{plate}\n" for plate in plates)

    files[POST_ANALYSIS_FILE] = dump_json(build_post_analysis(conn, cameras)) + "\n"

    detector_env, analysis_env = env_updates(conn)
    files[settings.detector_env_file] = update_env_text(
        _base_env_text(settings.project_dir, settings.detector_env_file), detector_env
    )
    files[settings.analysis_env_file] = update_env_text(
        _base_env_text(settings.project_dir, settings.analysis_env_file), analysis_env
    )
    return files


def read_current_files(settings: Settings, paths) -> dict[str, str | None]:
    current: dict[str, str | None] = {}
    for relative in paths:
        path = settings.project_dir / relative
        current[relative] = path.read_text(encoding="utf-8") if path.exists() else None
    return current
