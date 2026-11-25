"""Small per-stream FPS counter used by the DeepStream adapter."""

from __future__ import annotations

import logging
import time


logger = logging.getLogger(__name__)


class FpsCounter:
    def __init__(self, stream_id: int, label: str = ""):
        self.stream_id = stream_id
        self.label = label
        self.started_at: float | None = None
        self.frame_count = 0
        self.current_fps = 0.0

    def get_fps(self) -> float:
        now = time.monotonic()
        if self.started_at is None:
            self.started_at = now

        self.frame_count += 1
        elapsed = now - self.started_at
        if elapsed >= 1.0:
            self.current_fps = self.frame_count / elapsed
            if self.label:
                logger.info(
                    "[%s] FPS stream %s: %.2f", self.label, self.stream_id, self.current_fps
                )
            else:
                logger.info("FPS stream %s: %.2f", self.stream_id, self.current_fps)
            self.frame_count = 0
            self.started_at = now
        return self.current_fps
