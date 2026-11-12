"""Helpers for the Kafka contract shared by downstream analyzers."""

from __future__ import annotations

from typing import Mapping


def build_analysis_result(
    trigger_event: Mapping[str, object],
    *,
    analysis_cam_id: int,
    analysis_code: str,
    analysis_image: str,
    analysis_output: object,
) -> dict[str, object]:
    """Copy a trigger event and attach one downstream analysis result."""
    return {
        **trigger_event,
        "analysis_cam_id": analysis_cam_id,
        "analysis_code": analysis_code,
        "analysis_image": analysis_image,
        "analysis_output": analysis_output,
    }

