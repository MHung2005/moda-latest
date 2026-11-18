"""Detect camera sources that stopped delivering frames.

An RTSP-over-TCP source can stall without GStreamer ever posting EOS or ERROR, leaving the
detector alive but idle forever. The pipeline polls this watchdog and exits when a source
has been silent for too long so that Docker (restart: unless-stopped) starts it again.
"""

from __future__ import annotations

import os
import time
from typing import Callable, Iterable


DEFAULT_STALL_SECONDS = 60.0
DEFAULT_STARTUP_SECONDS = 120.0


def _env_seconds(name: str, default: float) -> float:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    value = float(raw)
    if value <= 0:
        raise ValueError(f"{name} must be greater than zero")
    return value


class StallWatchdog:
    """Track the last frame time of every source.

    Before its first frame a source gets `startup_seconds` (RTSP connect, decoder start);
    afterwards it may be silent at most `stall_seconds`. The default stall limit is above
    the 30 s Kafka publish timeout, which also blocks frame processing.
    """

    def __init__(
        self,
        source_ids: Iterable[int],
        *,
        stall_seconds: float = DEFAULT_STALL_SECONDS,
        startup_seconds: float = DEFAULT_STARTUP_SECONDS,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if stall_seconds <= 0 or startup_seconds <= 0:
            raise ValueError("watchdog limits must be greater than zero")
        self.stall_seconds = stall_seconds
        self.startup_seconds = startup_seconds
        self._clock = clock
        started_at = clock()
        self._started_at = {int(source_id): started_at for source_id in source_ids}
        self._last_frame_at: dict[int, float] = {}

    @classmethod
    def from_environment(cls, source_ids: Iterable[int]) -> "StallWatchdog":
        return cls(
            source_ids,
            stall_seconds=_env_seconds("MODA_SOURCE_STALL_SECONDS", DEFAULT_STALL_SECONDS),
            startup_seconds=_env_seconds(
                "MODA_SOURCE_STARTUP_SECONDS", DEFAULT_STARTUP_SECONDS
            ),
        )

    def mark(self, source_id: int) -> None:
        """Called for every processed frame; must stay cheap."""
        self._last_frame_at[source_id] = self._clock()

    def forget(self, source_id: int) -> None:
        """A source that ended with EOS is no longer expected to send frames."""
        self._started_at.pop(source_id, None)
        self._last_frame_at.pop(source_id, None)

    def stalled(self) -> list[tuple[int, float, bool]]:
        """[(source_id, silent_seconds, ever_received_a_frame)] over their limit."""
        now = self._clock()
        result = []
        for source_id, started_at in self._started_at.items():
            last = self._last_frame_at.get(source_id)
            if last is None:
                silent = now - started_at
                if silent > self.startup_seconds:
                    result.append((source_id, silent, False))
            else:
                silent = now - last
                if silent > self.stall_seconds:
                    result.append((source_id, silent, True))
        return result
