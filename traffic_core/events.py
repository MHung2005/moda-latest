"""Pure serialization of the legacy vehicle-detection Kafka contract."""

from __future__ import annotations


def build_vehicle_message(
    *,
    source_id: int,
    trigger_code: str,
    section_id: str,
    plate_number: str | None,
    image_path: str,
    timestamp: str,
    frame_number: int,
    video_fps: float,
    raw_plate: str,
    best_match: str,
    match_score,
    requires_search: bool,
) -> dict[str, object]:
    if video_fps <= 0:
        raise ValueError("video_fps must be greater than zero")

    return {
        "trigger_cam_id": source_id,
        "analysis_cam_id": -1,
        "trigger_code": trigger_code,
        "section_id": section_id,
        "analysis_code": "",
        "detected_object_id": plate_number,
        "trigger_image": image_path,
        "trigger_extra_data": "",
        "analysis_image": "",
        "analysis_extra_data": "",
        "analysis_output": "",
        "timestamp": timestamp,
        "frame_num": frame_number,
        "times_in_video": frame_number / video_fps,
        "raw_license_plate": raw_plate,
        "best_match_lp": best_match,
        "score_matching": match_score,
        "search": requires_search,
    }
