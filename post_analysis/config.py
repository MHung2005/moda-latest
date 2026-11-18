"""Validated configuration for the services that run after DeepStream."""

from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
from typing import Mapping


class AnalysisConfigError(ValueError):
    """The analysis config or one of its runtime variables is invalid."""


@dataclass(frozen=True)
class KafkaConfig:
    bootstrap_servers: str
    input_topic: str
    output_topic: str
    dead_letter_topic: str
    group_id: str
    publish_timeout_seconds: float


@dataclass(frozen=True)
class WorkerConfig:
    max_workers: int
    event_timeout_seconds: float
    max_attempts: int
    retry_backoff_seconds: float
    cancellation_grace_seconds: float


@dataclass(frozen=True)
class IoConfig:
    camera_open_timeout_ms: int
    camera_read_timeout_ms: int
    model_startup_attempts: int
    model_startup_delay_seconds: float


@dataclass(frozen=True)
class Crop:
    x_min: int
    x_max: int
    y_min: int
    y_max: int


@dataclass(frozen=True)
class WeightConfig:
    kafka: KafkaConfig
    worker: WorkerConfig
    io: IoConfig
    camera_url: str
    model_url: str
    crops: Mapping[str, Crop]
    analysis_codes: Mapping[str, str]
    output_root: str
    max_seconds: float
    confidence: float
    drop_threshold: int
    max_drop_count: int
    utc_offset_hours: int


def _object(value: object, field: str) -> dict:
    if not isinstance(value, dict):
        raise AnalysisConfigError(f"{field} must be a JSON object")
    return value


def _text(value: object, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise AnalysisConfigError(f"{field} must be a non-empty string")
    return value


def _number(value: object, field: str, cast):
    try:
        return cast(value)
    except (TypeError, ValueError) as error:
        raise AnalysisConfigError(f"{field} must be a valid {cast.__name__}") from error


def _positive(value: object, field: str, cast, *, allow_zero: bool = False):
    number = _number(value, field, cast)
    invalid = number < 0 if allow_zero else number <= 0
    if invalid:
        qualifier = "non-negative" if allow_zero else "greater than zero"
        raise AnalysisConfigError(f"{field} must be {qualifier}")
    return number


def _environment_value(
    environment: Mapping[str, str], env_name: object, field: str
) -> str:
    name = _text(env_name, field)
    value = environment.get(name)
    if not value:
        raise AnalysisConfigError(f"Missing runtime environment variable: {name}")
    return value


def _string_map(value: object, field: str) -> dict[str, str]:
    raw = _object(value, field)
    return {str(key): _text(item, f"{field}.{key}") for key, item in raw.items()}


def _model_url(base_url: str, model_name: object, field: str) -> str:
    return f"{base_url.rstrip('/')}/{_text(model_name, field)}"


def _load_raw(path: str | Path) -> dict:
    config_path = Path(path).resolve()
    try:
        return _object(
            json.loads(config_path.read_text(encoding="utf-8")), "analysis config"
        )
    except FileNotFoundError as error:
        raise AnalysisConfigError(f"Analysis config not found: {config_path}") from error
    except json.JSONDecodeError as error:
        raise AnalysisConfigError(f"Invalid JSON in {config_path}: {error}") from error


def _load_common(
    raw: dict,
    section: dict,
    environment: Mapping[str, str],
    default_group_id: str,
) -> tuple[KafkaConfig, WorkerConfig, IoConfig, str, int]:
    kafka = KafkaConfig(
        bootstrap_servers=_environment_value(
            environment,
            raw.get("kafka_bootstrap_servers_env", "MODA_KAFKA_BOOTSTRAP_SERVERS"),
            "kafka_bootstrap_servers_env",
        ),
        input_topic=_text(raw.get("input_topic"), "input_topic"),
        output_topic=_text(raw.get("output_topic"), "output_topic"),
        dead_letter_topic=_text(
            raw.get("dead_letter_topic", "AnalysisFailure_Gate1"),
            "dead_letter_topic",
        ),
        group_id=_text(section.get("group_id", default_group_id), "group_id"),
        publish_timeout_seconds=_positive(
            raw.get("publish_timeout_seconds", 30),
            "publish_timeout_seconds",
            float,
        ),
    )
    worker = WorkerConfig(
        max_workers=_positive(section.get("worker_count"), "worker_count", int),
        event_timeout_seconds=_positive(
            section.get("event_timeout_seconds"), "event_timeout_seconds", float
        ),
        max_attempts=_positive(section.get("max_attempts", 2), "max_attempts", int),
        retry_backoff_seconds=_positive(
            section.get("retry_backoff_seconds", 2),
            "retry_backoff_seconds",
            float,
            allow_zero=True,
        ),
        cancellation_grace_seconds=_positive(
            section.get("cancellation_grace_seconds", 3),
            "cancellation_grace_seconds",
            float,
            allow_zero=True,
        ),
    )
    io = IoConfig(
        camera_open_timeout_ms=_positive(
            section.get("camera_open_timeout_ms", 5_000),
            "camera_open_timeout_ms",
            int,
        ),
        camera_read_timeout_ms=_positive(
            section.get("camera_read_timeout_ms", 5_000),
            "camera_read_timeout_ms",
            int,
        ),
        model_startup_attempts=_positive(
            section.get("model_startup_attempts", 10),
            "model_startup_attempts",
            int,
        ),
        model_startup_delay_seconds=_positive(
            section.get("model_startup_delay_seconds", 2),
            "model_startup_delay_seconds",
            float,
            allow_zero=True,
        ),
    )
    triton_url = _environment_value(
        environment,
        raw.get("triton_url_env", "MODA_TRITON_URL"),
        "triton_url_env",
    )
    # Required, same rule as traffic_core.config.read_utc_offset_hours: never assume a zone.
    utc_offset_hours = raw.get("utc_offset_hours")
    if isinstance(utc_offset_hours, bool) or not isinstance(utc_offset_hours, int):
        raise AnalysisConfigError("utc_offset_hours must be an integer number of hours")
    if not -23 <= utc_offset_hours <= 23:
        raise AnalysisConfigError("utc_offset_hours must be between -23 and 23")
    return kafka, worker, io, triton_url, utc_offset_hours


def load_weight_config(
    path: str | Path, environment: Mapping[str, str] | None = None
) -> WeightConfig:
    values = os.environ if environment is None else environment
    raw = _load_raw(path)
    weight = _object(raw.get("weight"), "weight")
    kafka, worker, io, triton_url, utc_offset_hours = _load_common(
        raw, weight, values, "moda-weight-reader"
    )

    raw_crops = _object(weight.get("crops"), "weight.crops")
    crops: dict[str, Crop] = {}
    for trigger_code, coordinates in raw_crops.items():
        if not isinstance(coordinates, list) or len(coordinates) != 4:
            raise AnalysisConfigError(
                f"weight.crops.{trigger_code} must be [x_min, x_max, y_min, y_max]"
            )
        numbers = [
            _number(value, f"weight.crops.{trigger_code}", int)
            for value in coordinates
        ]
        crop = Crop(*numbers)
        if crop.x_min >= crop.x_max or crop.y_min >= crop.y_max:
            raise AnalysisConfigError(f"weight.crops.{trigger_code} is empty")
        crops[str(trigger_code)] = crop

    analysis_codes = _string_map(weight.get("analysis_codes"), "weight.analysis_codes")
    if set(crops) != set(analysis_codes):
        raise AnalysisConfigError(
            "weight.crops and weight.analysis_codes must have identical trigger codes"
        )

    confidence = _number(weight.get("confidence", 0.5), "weight.confidence", float)
    if not 0 <= confidence <= 1:
        raise AnalysisConfigError("weight.confidence must be between 0 and 1")

    return WeightConfig(
        kafka=kafka,
        worker=worker,
        io=io,
        camera_url=_environment_value(
            values, weight.get("camera_url_env"), "weight.camera_url_env"
        ),
        model_url=_model_url(triton_url, weight.get("model"), "weight.model"),
        crops=crops,
        analysis_codes=analysis_codes,
        output_root=_text(weight.get("output_root"), "weight.output_root"),
        max_seconds=_positive(
            weight.get("max_seconds", 60), "weight.max_seconds", float
        ),
        confidence=confidence,
        drop_threshold=_positive(
            weight.get("drop_threshold", 3000),
            "weight.drop_threshold",
            int,
            allow_zero=True,
        ),
        max_drop_count=_positive(
            weight.get("max_drop_count", 70), "weight.max_drop_count", int
        ),
        utc_offset_hours=utc_offset_hours,
    )
