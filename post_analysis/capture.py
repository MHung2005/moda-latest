"""OpenCV camera helpers with bounded connection and read attempts."""

from __future__ import annotations

import logging
from typing import Callable


logger = logging.getLogger(__name__)


def open_camera(cv2, url: str, open_timeout_ms: int, read_timeout_ms: int):
    """Open an FFmpeg capture and use timeout properties when OpenCV supports them."""
    backend = cv2.CAP_FFMPEG
    parameters: list[int] = []
    open_property = getattr(cv2, "CAP_PROP_OPEN_TIMEOUT_MSEC", None)
    read_property = getattr(cv2, "CAP_PROP_READ_TIMEOUT_MSEC", None)
    if open_property is not None:
        parameters.extend((open_property, open_timeout_ms))
    if read_property is not None:
        parameters.extend((read_property, read_timeout_ms))

    try:
        capture = cv2.VideoCapture(url, backend, parameters)
    except Exception as error:
        logger.warning(
            "This OpenCV build does not support capture timeout parameters: %s",
            error,
        )
        capture = cv2.VideoCapture(url, backend)

    if not capture.isOpened():
        capture.release()
        raise RuntimeError("Could not open RTSP camera")
    return capture


def open_with_retry(open_fn: Callable[[], object], attempts: int, should_continue: Callable[[], bool]):
    """Call open_fn up to `attempts` times; re-raise the last error when every attempt fails."""
    for attempt in range(1, attempts + 1):
        try:
            return open_fn()
        except Exception as error:
            if attempt == attempts or not should_continue():
                raise
            logger.warning("Camera open failed (attempt %d/%d): %s; retrying", attempt, attempts, error)
    raise RuntimeError("open_with_retry needs at least one attempt")


class ReconnectingCapture:
    """A capture whose read() reopens the stream when a read fails.

    The weighbridge camera is reached over a WAN link that sometimes stalls right after the
    RTSP session opens (one frame arrives, then nothing until the read timeout). Reopening
    the session usually recovers it within a few seconds. read() returns (False, None) only
    after `max_reconnects` reconnects in this capture's life have been used up, or when
    `should_continue()` turns false (event cancelled or out of time).
    """

    def __init__(
        self,
        open_fn: Callable[[], object],
        capture: object,
        max_reconnects: int,
        should_continue: Callable[[], bool],
    ) -> None:
        self._open = open_fn
        self._capture = capture
        self._max_reconnects = max_reconnects
        self._should_continue = should_continue
        self.reconnects = 0

    def read(self):
        while True:
            if self._capture is not None:
                ok, frame = self._capture.read()
                if ok:
                    return True, frame
                self._capture.release()
                self._capture = None
            if self.reconnects >= self._max_reconnects or not self._should_continue():
                return False, None
            self.reconnects += 1
            logger.warning(
                "Camera read failed; reopening the stream (reconnect %d/%d)",
                self.reconnects,
                self._max_reconnects,
            )
            try:
                self._capture = self._open()
            except Exception as error:
                logger.warning("Camera reopen failed (reconnect %d/%d): %s",
                               self.reconnects, self._max_reconnects, error)

    def release(self) -> None:
        if self._capture is not None:
            self._capture.release()
            self._capture = None
