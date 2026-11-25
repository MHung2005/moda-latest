"""Read-only API for checking plate reading frame by frame (/api/plate-check).

The detector keeps every frame it read a plate from for the last vehicles of each lane:

    runtime/vehicle/<lane>/frames/<section_id>/
        <frame_num:07d>.jpg   crop of the vehicle
        frames.jsonl          one JSON object per frame
        summary.json          written once the vehicle was published

Any logged-in user may read. Only files under get_settings().runtime_dir are touched.
"""

from __future__ import annotations

import json
from pathlib import Path
import re
import uuid

from fastapi import APIRouter, Depends, HTTPException

from .auth import current_user
from .results import vehicle_image_relpath
from .settings import get_settings


router = APIRouter(prefix="/api/plate-check")
User = Depends(current_user)

LANE_PATTERN = re.compile(r"^[a-z0-9_]+$")
VEHICLES_PER_LANE = 5
FRAMES_FILE = "frames.jsonl"
SUMMARY_FILE = "summary.json"


def _vehicle_root() -> Path:
    return get_settings().runtime_dir / "vehicle"


def _check_lane(lane: str) -> str:
    if not LANE_PATTERN.fullmatch(lane):
        raise HTTPException(400, "Mã làn không hợp lệ")
    return lane


def _check_section(section_id: str) -> str:
    try:
        parsed = uuid.UUID(section_id)
    except ValueError as error:
        raise HTTPException(400, "Mã xe (section_id) không hợp lệ") from error
    if str(parsed) != section_id.lower():
        raise HTTPException(400, "Mã xe (section_id) không hợp lệ")
    return str(parsed)


def _is_section_name(name: str) -> bool:
    try:
        return str(uuid.UUID(name)) == name.lower()
    except ValueError:
        return False


def _read_frames(directory: Path) -> list[dict]:
    """frames.jsonl, skipping lines that are not JSON objects (the last one may be half written)."""
    path = directory / FRAMES_FILE
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    frames = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            item = json.loads(line)
        except ValueError:
            continue
        if isinstance(item, dict):
            frames.append(item)
    return frames


def _read_summary(directory: Path) -> dict | None:
    try:
        data = json.loads((directory / SUMMARY_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _frame_num(frame: dict) -> int | None:
    value = frame.get("frame_num")
    if isinstance(value, bool):
        return None
    try:
        return int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def _frame_relpath(lane: str, section_id: str, frame_num: int) -> str:
    return f"vehicle/{lane}/frames/{section_id}/{frame_num:07d}.jpg"


def _capture_image(lane: str, section_id: str, directory: Path, summary: dict | None) -> str | None:
    """The published capture image, else the crop of the capture frame."""
    if not summary:
        return None
    image = summary.get("image_path")
    if isinstance(image, str) and image:
        relpath = vehicle_image_relpath(image, lane)
        if relpath and (get_settings().runtime_dir / relpath).is_file():
            return relpath
    frame_num = _frame_num({"frame_num": summary.get("capture_frame")})
    if frame_num is not None and (directory / f"{frame_num:07d}.jpg").is_file():
        return _frame_relpath(lane, section_id, frame_num)
    return None


def _modified(directory: Path) -> float:
    times = []
    for path in (directory, directory / FRAMES_FILE, directory / SUMMARY_FILE):
        try:
            times.append(path.stat().st_mtime)
        except OSError:
            pass
    return max(times, default=0.0)


def _vehicle_overview(lane: str, directory: Path) -> dict:
    section_id = directory.name.lower()
    frames = _read_frames(directory)
    summary = _read_summary(directory)
    return {
        "section_id": section_id,
        "summary": summary,
        "capture_image_path": _capture_image(lane, section_id, directory, summary),
        "frame_count": len(frames),
        "valid_frame_count": sum(1 for frame in frames if frame.get("valid_plate")),
        "first_ts": frames[0].get("ts") if frames else None,
        "last_ts": frames[-1].get("ts") if frames else None,
        "updated_at": _modified(directory),
    }


def _lane_sections(lane: str) -> list[Path]:
    root = _vehicle_root() / lane / "frames"
    try:
        entries = [entry for entry in root.iterdir() if entry.is_dir() and _is_section_name(entry.name)]
    except OSError:
        return []
    return sorted(entries, key=_modified, reverse=True)


@router.get("")
def list_vehicles(_: dict = User) -> dict:
    """Up to 5 latest vehicles per lane, newest first."""
    root = _vehicle_root()
    try:
        lanes = sorted(entry.name for entry in root.iterdir() if entry.is_dir() and LANE_PATTERN.fullmatch(entry.name))
    except OSError:
        lanes = []
    return {
        "lanes": [
            {
                "lane": lane,
                "vehicles": [_vehicle_overview(lane, d) for d in _lane_sections(lane)[:VEHICLES_PER_LANE]],
            }
            for lane in lanes
        ]
    }


@router.get("/{lane}/{section_id}")
def vehicle_frames(lane: str, section_id: str, _: dict = User) -> dict:
    lane = _check_lane(lane)
    section_id = _check_section(section_id)
    lane_root = (_vehicle_root() / lane / "frames").resolve()
    directory = (lane_root / section_id).resolve()
    if directory.parent != lane_root or not directory.is_dir():
        raise HTTPException(404, "Không tìm thấy dữ liệu khung hình của xe này")

    frames = []
    for frame in _read_frames(directory):
        number = _frame_num(frame)
        image = None
        if number is not None and (directory / f"{number:07d}.jpg").is_file():
            image = _frame_relpath(lane, section_id, number)
        frames.append({**frame, "frame_num": number, "image_path": image})
    frames.sort(key=lambda item: (item["frame_num"] is None, item["frame_num"] or 0))

    summary = _read_summary(directory)
    return {
        "lane": lane,
        "section_id": section_id,
        "summary": summary,
        "capture_image_path": _capture_image(lane, section_id, directory, summary),
        "frames": frames,
    }
