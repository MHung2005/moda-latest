"""Load one complete, validated Jetson deployment configuration."""

from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
from typing import Mapping


Point = tuple[int, int]
Polygon = tuple[Point, ...]


class SiteConfigError(ValueError):
    """The static site file or its required environment is invalid."""


@dataclass(frozen=True)
class SiteConfig:
    name: str
    camera_urls: tuple[str, ...]
    kafka_bootstrap_servers: str
    trigger_codes: Mapping[int, str]
    capture_zones: Mapping[int, Polygon]
    inference_zones: Mapping[int, Polygon]
    tracker_config_path: str
    vehicle_detection_config_path: str
    plate_detection_config_path: str
    plate_recognition_config_path: str
    reference_plate_path: str
    # Local time zone of the site (dated image folders). Required: never assume one.
    utc_offset_hours: int
    vehicle_confidence: float = 0.8
    plate_character_confidence: float = 0.8
    plate_collection_seconds: float = 4.0
    output_topic: str = "VehicleDetection_Gate1"
    output_partition: int = 0
    publish_timeout_seconds: float = 30.0
    video_fps: float = 10.0
    vehicle_image_root: str = "/data/vehicle"


def _require_object(value: object, field_name: str) -> dict:
    if not isinstance(value, dict):
        raise SiteConfigError(f"{field_name} must be a JSON object")
    return value


def _read_polygon(value: object, field_name: str) -> Polygon:
    if not isinstance(value, list) or len(value) < 3:
        raise SiteConfigError(f"{field_name} must contain at least three points")

    points: list[Point] = []
    for index, point in enumerate(value):
        if not isinstance(point, list) or len(point) != 2:
            raise SiteConfigError(f"{field_name}[{index}] must be [x, y]")
        x, y = point
        if not isinstance(x, int) or not isinstance(y, int):
            raise SiteConfigError(f"{field_name}[{index}] coordinates must be integers")
        points.append((x, y))
    return tuple(points)


def _read_source_map(raw: dict, field_name: str) -> dict[int, object]:
    value = _require_object(raw.get(field_name), field_name)
    try:
        return {int(source_id): item for source_id, item in value.items()}
    except (TypeError, ValueError) as error:
        raise SiteConfigError(f"{field_name} keys must be integer source IDs") from error


def _resolve_path(config_path: Path, value: object, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise SiteConfigError(f"{field_name} must be a non-empty path")
    if value.startswith("/"):
        return value
    path = Path(value)
    if not path.is_absolute():
        path = (config_path.parent / path).resolve()
    return str(path)


def read_utc_offset_hours(raw: dict) -> int:
    """Whole-hour UTC offset of the site; shared rule with post_analysis.config."""
    value = raw.get("utc_offset_hours")
    if isinstance(value, bool) or not isinstance(value, int):
        raise SiteConfigError("utc_offset_hours must be an integer number of hours")
    if not -23 <= value <= 23:
        raise SiteConfigError("utc_offset_hours must be between -23 and 23")
    return value


def _read_environment(
    raw: dict,
    environment: Mapping[str, str],
) -> tuple[tuple[str, ...], str]:
    camera_envs = raw.get("camera_url_envs")
    if not isinstance(camera_envs, list) or not camera_envs or not all(
        isinstance(name, str) and name for name in camera_envs
    ):
        raise SiteConfigError("camera_url_envs must be a non-empty list of names")

    kafka_env = raw.get(
        "kafka_bootstrap_servers_env",
        "MODA_KAFKA_BOOTSTRAP_SERVERS",
    )
    if not isinstance(kafka_env, str) or not kafka_env:
        raise SiteConfigError("kafka_bootstrap_servers_env must be a non-empty name")

    required_names = [*camera_envs, kafka_env]
    missing = [name for name in required_names if not environment.get(name)]
    if missing:
        raise SiteConfigError(
            "Missing runtime environment variable(s): " + ", ".join(missing)
        )
    return (
        tuple(environment[name] for name in camera_envs),
        environment[kafka_env],
    )


def load_site_config(
    path: str | Path,
    environment: Mapping[str, str] | None = None,
) -> SiteConfig:
    config_path = Path(path).resolve()
    try:
        raw = json.loads(config_path.read_text(encoding="utf-8"))
    except FileNotFoundError as error:
        raise SiteConfigError(f"Site config not found: {config_path}") from error
    except json.JSONDecodeError as error:
        raise SiteConfigError(f"Invalid JSON in {config_path}: {error}") from error
    raw = _require_object(raw, "site config")

    required = {
        "name",
        "camera_url_envs",
        "trigger_codes",
        "capture_zones",
        "inference_zones",
        "models",
        "reference_plate_path",
        "utc_offset_hours",
    }
    missing_fields = sorted(required.difference(raw))
    if missing_fields:
        raise SiteConfigError(
            "Missing site config fields: " + ", ".join(missing_fields)
        )

    values = os.environ if environment is None else environment
    camera_urls, kafka_servers = _read_environment(raw, values)
    expected_sources = set(range(len(camera_urls)))

    trigger_codes = {
        source_id: str(value)
        for source_id, value in _read_source_map(raw, "trigger_codes").items()
    }
    capture_zones = {
        source_id: _read_polygon(value, f"capture_zones.{source_id}")
        for source_id, value in _read_source_map(raw, "capture_zones").items()
    }
    inference_zones = {
        source_id: _read_polygon(value, f"inference_zones.{source_id}")
        for source_id, value in _read_source_map(raw, "inference_zones").items()
    }
    for field_name, mapping in (
        ("trigger_codes", trigger_codes),
        ("capture_zones", capture_zones),
        ("inference_zones", inference_zones),
    ):
        if set(mapping) != expected_sources:
            raise SiteConfigError(
                f"{field_name} keys must be {sorted(expected_sources)}, "
                f"got {sorted(mapping)}"
            )

    models = _require_object(raw["models"], "models")
    model_fields = {
        "tracker": "tracker_config_path",
        "vehicle_detection": "vehicle_detection_config_path",
        "plate_detection": "plate_detection_config_path",
        "plate_recognition": "plate_recognition_config_path",
    }
    missing_models = sorted(set(model_fields).difference(models))
    if missing_models:
        raise SiteConfigError("Missing model paths: " + ", ".join(missing_models))

    try:
        vehicle_confidence = float(raw.get("vehicle_confidence", 0.8))
        plate_confidence = float(raw.get("plate_character_confidence", 0.8))
        collection_seconds = float(raw.get("plate_collection_seconds", 4.0))
        video_fps = float(raw.get("video_fps", 10.0))
        output_partition = int(raw.get("output_partition", 0))
        publish_timeout_seconds = float(raw.get("publish_timeout_seconds", 30.0))
    except (TypeError, ValueError) as error:
        raise SiteConfigError("Numeric site settings contain an invalid value") from error
    if not 0.0 <= vehicle_confidence <= 1.0:
        raise SiteConfigError("vehicle_confidence must be between 0 and 1")
    if not 0.0 <= plate_confidence <= 1.0:
        raise SiteConfigError("plate_character_confidence must be between 0 and 1")
    if collection_seconds <= 0:
        raise SiteConfigError("plate_collection_seconds must be greater than zero")
    if video_fps <= 0:
        raise SiteConfigError("video_fps must be greater than zero")
    if output_partition < 0:
        raise SiteConfigError("output_partition must not be negative")
    if publish_timeout_seconds <= 0:
        raise SiteConfigError("publish_timeout_seconds must be greater than zero")
    if any(not value for value in trigger_codes.values()):
        raise SiteConfigError("trigger_codes values must not be empty")
    output_topic = raw.get("output_topic", "VehicleDetection_Gate1")
    if not isinstance(output_topic, str) or not output_topic:
        raise SiteConfigError("output_topic must be a non-empty string")

    model_paths = {
        target: _resolve_path(config_path, models[source], f"models.{source}")
        for source, target in model_fields.items()
    }
    return SiteConfig(
        name=str(raw["name"]),
        camera_urls=camera_urls,
        kafka_bootstrap_servers=kafka_servers,
        trigger_codes=trigger_codes,
        capture_zones=capture_zones,
        inference_zones=inference_zones,
        reference_plate_path=_resolve_path(
            config_path,
            raw["reference_plate_path"],
            "reference_plate_path",
        ),
        utc_offset_hours=read_utc_offset_hours(raw),
        vehicle_confidence=vehicle_confidence,
        plate_character_confidence=plate_confidence,
        plate_collection_seconds=collection_seconds,
        output_topic=output_topic,
        output_partition=output_partition,
        publish_timeout_seconds=publish_timeout_seconds,
        video_fps=video_fps,
        vehicle_image_root=str(raw.get("vehicle_image_root", "/data/vehicle")),
        **model_paths,
    )
