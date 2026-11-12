"""Request/response models. Constraints mirror traffic_core/config.py and
post_analysis/config.py so that the UI rejects what the runtime would reject."""

from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field, field_validator, model_validator


Point = tuple[int, int]

KAFKA_ENV = "MODA_KAFKA_BOOTSTRAP_SERVERS"
TRITON_ENV = "MODA_TRITON_URL"


# --- auth ----------------------------------------------------------------------------


class LoginRequest(BaseModel):
    username: str
    password: str


class UserOut(BaseModel):
    id: int
    username: str
    role: Literal["admin", "viewer"]


class UserCreate(BaseModel):
    username: str = Field(min_length=3, max_length=64)
    password: str = Field(min_length=8)
    role: Literal["admin", "viewer"] = "viewer"


class PasswordChange(BaseModel):
    password: str = Field(min_length=8)


# --- cameras -------------------------------------------------------------------------


class CameraIn(BaseModel):
    code: str = Field(pattern=r"^[a-z0-9_]+$", max_length=64)
    name: str = Field(min_length=1, max_length=128)
    purpose: Literal["lane", "weight"]
    env_name: str = Field(pattern=r"^[A-Z][A-Z0-9_]*$", max_length=128)
    # Omitted on update = keep the stored URL.
    rtsp_url: Optional[str] = None

    @field_validator("rtsp_url")
    @classmethod
    def _url(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return value
        value = value.strip()
        if not value.startswith(("rtsp://", "rtsps://", "http://", "https://")):
            raise ValueError("URL phải bắt đầu bằng rtsp:// (hoặc http/https)")
        return value


class CameraOut(BaseModel):
    id: int
    code: str
    name: str
    purpose: str
    env_name: str
    rtsp_url_masked: str
    frame_width: Optional[int]
    frame_height: Optional[int]
    updated_at: str


# --- reference plates ----------------------------------------------------------------


class PlateListIn(BaseModel):
    name: str = Field(pattern=r"^[A-Za-z0-9_-]+$", max_length=64)


class PlateListOut(BaseModel):
    id: int
    name: str
    file_name: str
    plate_count: int


class PlatesIn(BaseModel):
    plates: list[str]

    @field_validator("plates")
    @classmethod
    def _normalise(cls, values: list[str]) -> list[str]:
        cleaned: list[str] = []
        seen: set[str] = set()
        for value in values:
            plate = value.strip().upper().replace("-", "").replace(".", "").replace(" ", "")
            if not plate:
                continue
            if not plate.isalnum():
                raise ValueError(f"Biển số không hợp lệ: {value!r}")
            if plate not in seen:
                seen.add(plate)
                cleaned.append(plate)
        return cleaned


# --- sites ---------------------------------------------------------------------------


class SiteIn(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    vehicle_confidence: float = Field(ge=0, le=1)
    plate_character_confidence: float = Field(ge=0, le=1)
    plate_collection_seconds: float = Field(gt=0)
    video_fps: float = Field(gt=0)
    output_topic: str = Field(min_length=1)
    output_partition: int = Field(ge=0)
    publish_timeout_seconds: float = Field(gt=0)
    model_tracker: str = Field(min_length=1)
    model_vehicle_detection: str = Field(min_length=1)
    model_plate_detection: str = Field(min_length=1)
    model_plate_recognition: str = Field(min_length=1)
    reference_plate_list_id: int


class SourceIn(BaseModel):
    camera_id: int
    trigger_code: str = Field(min_length=1, max_length=64)


class ZoneIn(BaseModel):
    points: list[Point]

    @field_validator("points")
    @classmethod
    def _polygon(cls, points: list[Point]) -> list[Point]:
        if len(points) < 3:
            raise ValueError("Vùng phải có ít nhất 3 điểm")
        for x, y in points:
            if x < 0 or y < 0:
                raise ValueError("Toạ độ không được âm")
        return points


# --- system / analyzers --------------------------------------------------------------


class SystemSettings(BaseModel):
    kafka_bootstrap_servers: str = Field(min_length=1)
    triton_url: str = Field(min_length=1)
    input_topic: str = Field(min_length=1)
    output_topic: str = Field(min_length=1)
    dead_letter_topic: str = Field(min_length=1)
    publish_timeout_seconds: float = Field(gt=0)
    utc_offset_hours: int = Field(ge=-23, le=23)


class WorkerSettings(BaseModel):
    group_id: str = Field(min_length=1)
    output_root: str = Field(min_length=1)
    worker_count: int = Field(gt=0)
    event_timeout_seconds: float = Field(gt=0)
    max_attempts: int = Field(gt=0)
    retry_backoff_seconds: float = Field(ge=0)
    cancellation_grace_seconds: float = Field(ge=0)
    camera_open_timeout_ms: int = Field(gt=0)
    camera_read_timeout_ms: int = Field(gt=0)
    model_startup_attempts: int = Field(gt=0)
    model_startup_delay_seconds: float = Field(ge=0)


class WeightSettings(WorkerSettings):
    camera_id: int
    model: str = Field(min_length=1)
    max_seconds: float = Field(gt=0)
    confidence: float = Field(ge=0, le=1)
    drop_threshold: int = Field(ge=0)
    max_drop_count: int = Field(gt=0)


ANALYZER_MODELS = {"weight": WeightSettings}


class WeightLaneIn(BaseModel):
    analysis_code: str = Field(min_length=1)
    crop_x_min: int = Field(ge=0)
    crop_x_max: int = Field(gt=0)
    crop_y_min: int = Field(ge=0)
    crop_y_max: int = Field(gt=0)

    @model_validator(mode="after")
    def _non_empty(self) -> "WeightLaneIn":
        if self.crop_x_min >= self.crop_x_max or self.crop_y_min >= self.crop_y_max:
            raise ValueError("Vùng crop rỗng: cần x_min < x_max và y_min < y_max")
        return self


# --- apply ---------------------------------------------------------------------------


class ApplyRequest(BaseModel):
    note: str = Field(default="", max_length=500)
