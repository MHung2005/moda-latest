"""Triton-backed Ultralytics model loading with bounded startup retries."""

from __future__ import annotations

import logging
import time


logger = logging.getLogger(__name__)


def load_yolo(
    url: str,
    *,
    task: str,
    attempts: int,
    delay_seconds: float,
):
    from ultralytics import YOLO

    last_error: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            return YOLO(url, task=task)
        except Exception as error:
            last_error = error
            if attempt == attempts:
                break
            logger.warning(
                "Model %s is not ready (attempt %s/%s): %s",
                url,
                attempt,
                attempts,
                error,
            )
            time.sleep(delay_seconds)
    raise RuntimeError(f"Model did not become ready: {url}") from last_error

